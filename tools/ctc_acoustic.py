"""CPU streaming Chinese CTC posteriors for the PC reading prototype.

Model protocol: k2-fsa/icefall zipformer/onnx_pretrained-streaming-ctc.py.
This optional backend is independent of the small-board MFCC executable.
"""

import hashlib
import wave
from pathlib import Path


MODEL_REVISION = "f29132af655b91f258800df19c5b5d9b9281a5cd"
MODEL_REPO = "csukuangfj/sherpa-onnx-streaming-zipformer-ctc-zh-int8-2025-06-30"
MODEL_HASHES = {"model.int8.onnx": "24ffdc19ba9aaed5a6a9beaede1e087745217d82425cf4041bca0c696661801e",
                "tokens.txt": "6193c7ea1c96d0d9a1e9652789b40d13a8a913b434a5451e93158f5a09fd6652"}


def verify_model(directory):
    for name, expected in MODEL_HASHES.items():
        with (Path(directory) / name).open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                raise ValueError(f"CTC model checksum mismatch: {name}")


def read_pcm_wav(path):
    import numpy as np
    with wave.open(str(path), "rb") as stream:
        if (stream.getframerate(), stream.getnchannels(), stream.getsampwidth()) != (16000, 1, 2):
            raise ValueError("CTC input must be 16 kHz mono PCM16 WAV")
        count = stream.getnframes()
        if not 0 < count <= 16000 * 120:
            raise ValueError("CTC PC recordings must be between 1 sample and 120 seconds")
        data = stream.readframes(count)
        if len(data) != count * 2:
            raise ValueError("Truncated CTC input WAV")
    return np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768


class ZipformerCtc:
    frame_seconds = 0.04
    # PCM16 digital zero is below this; keep the gate conservative for quiet speech.
    frame_rms_floor = 0.000001
    blank_id = 0

    def __init__(self, directory):
        import onnxruntime as ort
        verify_model(directory)
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(Path(directory) / "model.int8.onnx"),
                                           sess_options=options, providers=["CPUExecutionProvider"])
        metadata = self.session.get_modelmeta().custom_metadata_map
        if metadata.get("model_type") != "zipformer2":
            raise ValueError("Expected a streaming Zipformer2 CTC model")
        if metadata.get("feature") != "whisper":
            raise ValueError("Pinned CTC model must declare its Whisper feature frontend")
        self.chunk_frames = int(metadata["T"])
        self.chunk_shift = int(metadata["decode_chunk_len"])
        self.inputs = self.session.get_inputs()
        self.output_names = ["log_probs"] + [f"new_{item.name}" for item in self.inputs[1:]]
        self.tokens = {}
        vocabulary_size = self.session.get_outputs()[0].shape[-1]
        for line in (Path(directory) / "tokens.txt").read_text(encoding="utf-8").splitlines():
            token, index = line.rsplit(maxsplit=1)
            if token.startswith("#") and int(index) >= vocabulary_size:
                continue  # FST disambiguation symbols are not acoustic labels.
            self.tokens[int(index)] = token
        if sorted(self.tokens) != list(range(vocabulary_size)):
            raise ValueError("CTC token table does not cover the model vocabulary")
        self.token_ids = {token: index for index, token in self.tokens.items()}
        self._projection = list(range(len(self.tokens)))
        for index, token in self.tokens.items():
            if token == "▁":
                self._projection[index] = self.blank_id
            elif token.startswith("▁") and len(token) == 2 and token[1:] in self.token_ids:
                self._projection[index] = self.token_ids[token[1:]]

    def new_stream(self):
        return CtcStream(self)

    def encode_text(self, text):
        return self.alignment_tokens(text)[0]

    def alignment_tokens(self, text):
        labels, spans = [], []
        for char in text:
            start = len(labels)
            if char in self.token_ids:
                labels.append(self.token_ids[char])
            elif "▁" + char in self.token_ids:
                labels.append(self.token_ids["▁" + char])
            else:
                try:
                    labels.extend(self.token_ids[f"<0x{byte:02X}>"] for byte in char.encode("utf-8"))
                except KeyError as error:
                    raise ValueError(f"Reference character absent from CTC vocabulary: {char}") from error
            spans.append((start, len(labels)))
        return labels, spans

    def project_scores(self, scores):
        """Pool one-character word-initial aliases and ignore a space-only token.

        Rare characters retain their UTF-8 byte fallback labels. Pooling imposes
        the stricter character-CTC repeat rule even if two raw aliases differ.
        """
        import numpy as np
        probabilities = np.exp(scores - np.max(scores, axis=1, keepdims=True))
        probabilities /= probabilities.sum(axis=1, keepdims=True)
        projected = np.zeros_like(probabilities)
        np.add.at(projected.T, self._projection, probabilities.T)
        return np.log(np.maximum(projected, np.finfo(projected.dtype).tiny))

    def decode_ids(self, token_ids):
        data = bytearray()
        for index in token_ids:
            token = self.tokens[index]
            if token.startswith("<0x") and token.endswith(">"):
                data.append(int(token[3:-1], 16))
            elif not token.startswith("<"):
                data.extend(token.replace("▁", " ").encode("utf-8"))
            else:
                data.extend("�".encode("utf-8"))
        return data.decode("utf-8", errors="replace").strip()


class CtcStream:
    """Independent encoder state, online fbank and bounded waveform ingestion."""
    def __init__(self, model):
        import kaldi_native_fbank as knf
        import numpy as np
        self.model, self.processed, self.samples, self.finished = model, 0, 0, False
        options = knf.WhisperFeatureOptions()
        options.frame_opts.samp_freq = 16000
        options.frame_opts.dither = 0
        options.frame_opts.snip_edges = False
        options.dim = 80
        self.fbank = knf.OnlineWhisperFbank(options)
        self.states = {}
        for item in model.inputs[1:]:
            shape = [1 if dim == "N" else dim for dim in item.shape]
            if any(type(dim) is not int or dim <= 0 for dim in shape):
                raise ValueError(f"Unsupported CTC state shape: {item.name} {item.shape}")
            dtype = {"tensor(float)": np.float32, "tensor(int64)": np.int64}.get(item.type)
            if dtype is None:
                raise ValueError(f"Unsupported CTC state type: {item.type}")
            self.states[item.name] = np.zeros(shape, dtype=dtype)

    def accept(self, samples, final=False):
        import numpy as np
        samples = np.asarray(samples, dtype=np.float32)
        if (self.finished or samples.ndim != 1 or len(samples) > 16000 or
                not np.isfinite(samples).all() or np.any(np.abs(samples) > 1)):
            raise ValueError("CTC stream accepts at most one second of finite normalized PCM per call")
        self.samples += len(samples)
        self.fbank.accept_waveform(16000, samples)
        if final:
            # Flush encoder right context; returned timestamps remain on the input
            # clock. The caller must exclude padding beyond the source duration.
            self.fbank.accept_waveform(16000, np.zeros(16000, dtype=np.float32))
            self.fbank.input_finished()
            self.finished = True
        chunks = []
        while self.fbank.num_frames_ready - self.processed >= self.model.chunk_frames:
            features = np.stack([self.fbank.get_frame(self.processed + offset)
                                 for offset in range(self.model.chunk_frames)])[None]
            # The model metadata selects the Whisper frontend. Normalize each
            # overlapping chunk exactly as sherpa-onnx's streaming CTC runtime.
            features = np.log10(np.maximum(features, 1e-10))
            features = (np.maximum(features, features.max() - 8) + 4) / 4
            result = self.model.session.run(self.model.output_names, {"x": features, **self.states})
            scores = result[0][0]
            if (scores.ndim != 2 or len(scores) * 4 != self.model.chunk_shift or
                    scores.shape[1] != len(self.model.tokens) or not np.isfinite(scores).all()):
                raise ValueError(f"Unexpected CTC posterior: shape={scores.shape}, "
                                 f"chunkShift={self.model.chunk_shift}, vocabulary={len(self.model.tokens)}, "
                                 f"finite={np.isfinite(scores).all()}")
            self.states = dict(zip(self.states, result[1:]))
            chunks.append(self.model.project_scores(scores))
            self.processed += self.model.chunk_shift
            self.fbank.pop(self.model.chunk_shift)
        return np.concatenate(chunks) if chunks else np.empty((0, len(self.model.tokens)), dtype=np.float32)
