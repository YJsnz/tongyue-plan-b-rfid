#include "audio_locator.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <iostream>
#include <limits>
#include <set>
#include <stdexcept>
#include <utility>

#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#endif

namespace {

constexpr char kBankMagic[] = "TYMFCC01";
constexpr std::uint32_t kBankMaxReferences = 1024;
constexpr std::size_t kBankMaxFrames = 160000;
static_assert(sizeof(float) == 4 && std::numeric_limits<float>::is_iec559, "Reference bank requires binary32");

std::uint32_t little_endian(std::istream& input, int bytes) {
    std::uint32_t value = 0;
    for (int i = 0; i < bytes; ++i) {
        const int byte = input.get();
        if (byte == std::char_traits<char>::eof()) { throw std::runtime_error("Truncated binary input"); }
        value |= static_cast<std::uint32_t>(byte) << (i * 8);
    }
    return value;
}

std::string fourcc(std::istream& input) {
    char value[4];
    if (!input.read(value, 4)) { throw std::runtime_error("Truncated WAV chunk header"); }
    return {value, 4};
}

class WavInput {
public:
    explicit WavInput(const std::string& path) : input_(path, std::ios::binary) {
        if (!input_) { throw std::runtime_error("Cannot open " + path); }
        if (fourcc(input_) != "RIFF") { throw std::runtime_error("Expected RIFF WAV: " + path); }
        const auto riff_size = little_endian(input_, 4);
        const auto riff_end = static_cast<std::streamoff>(riff_size) + 8;
        if (fourcc(input_) != "WAVE") { throw std::runtime_error("Expected WAVE: " + path); }
        input_.seekg(0, std::ios::end);
        if (input_.tellg() < riff_end) { throw std::runtime_error("Truncated RIFF: " + path); }
        input_.seekg(12);
        bool pcm = false;
        while (input_.tellg() + std::streamoff{8} <= riff_end) {
            const auto chunk = fourcc(input_);
            const auto size = little_endian(input_, 4);
            const auto end = input_.tellg() + (static_cast<std::streamoff>(size) + (size & 1));
            if (end > riff_end) { throw std::runtime_error("WAV chunk exceeds RIFF: " + path); }
            if (chunk == "fmt " && size >= 16) {
                const auto encoding = little_endian(input_, 2), channels = little_endian(input_, 2);
                const auto rate = little_endian(input_, 4);
                little_endian(input_, 4);
                const auto stride = little_endian(input_, 2), bits = little_endian(input_, 2);
                pcm = encoding == 1 && channels == 1 && rate == tongyue::kSampleRate && stride == 2 && bits == 16;
            } else if (chunk == "data") {
                if (!pcm || (size & 1) || size == 0) { break; }
                remaining_ = size / 2;
                return;
            }
            input_.seekg(end);
        }
        throw std::runtime_error("Expected 16 kHz mono PCM16 WAV: " + path);
    }

    std::size_t remaining() const { return remaining_; }

    std::size_t read(std::int16_t* buffer, std::size_t capacity) {
        const std::size_t count = std::min(capacity, remaining_);
        for (std::size_t i = 0; i < count; ++i) {
            const auto bits = static_cast<int>(little_endian(input_, 2));
            buffer[i] = static_cast<std::int16_t>(bits < 32768 ? bits : bits - 65536);
        }
        remaining_ -= count;
        return count;
    }

private:
    std::ifstream input_;
    std::size_t remaining_ = 0;
};

bool valid_id(const std::string& id) {
    return !id.empty() && id.size() <= 64 &&
        id.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-") == std::string::npos;
}

tongyue::Reference from_wav(const std::string& argument) {
    const auto separator = argument.find('=');
    if (separator == std::string::npos) { throw std::runtime_error("Expected ID=PATH"); }
    const std::string id = argument.substr(0, separator);
    if (!valid_id(id)) { throw std::runtime_error("Invalid segment ID: " + id); }
    WavInput wav(argument.substr(separator + 1));
    if (wav.remaining() > 30 * tongyue::kSampleRate) { throw std::runtime_error("Reference WAV exceeds 30 seconds: " + id); }
    std::vector<std::int16_t> pcm(wav.remaining());
    wav.read(pcm.data(), pcm.size());
    return tongyue::make_reference(id, pcm);
}

void write_u32(std::ostream& output, std::uint32_t value) {
    unsigned char bytes[4];
    for (int i = 0; i < 4; ++i) { bytes[i] = static_cast<unsigned char>(value >> (i * 8)); }
    output.write(reinterpret_cast<const char*>(bytes), sizeof(bytes));
}

void write_bank(const std::string& path, const std::vector<tongyue::Reference>& refs) {
    if (refs.empty() || refs.size() > kBankMaxReferences) { throw std::runtime_error("Invalid reference bank size"); }
    std::set<std::string> ids;
    std::size_t total = 0;
    for (const auto& ref : refs) {
        total += ref.frames.size();
        if (!ids.insert(ref.segment_id).second || total > kBankMaxFrames) {
            throw std::runtime_error("Duplicate ID or reference bank exceeds frame limit");
        }
    }
    std::ofstream output(path, std::ios::binary);
    output.exceptions(std::ios::badbit | std::ios::failbit);
    output.write(kBankMagic, sizeof(kBankMagic) - 1);
    write_u32(output, static_cast<std::uint32_t>(refs.size()));
    for (const auto& ref : refs) {
        write_u32(output, static_cast<std::uint32_t>(ref.segment_id.size()));
        output.write(ref.segment_id.data(), ref.segment_id.size());
        write_u32(output, static_cast<std::uint32_t>(ref.frames.size()));
        for (const auto& frame : ref.frames) {
            for (float value : frame) {
                std::uint32_t bits = 0;
                std::memcpy(&bits, &value, sizeof(bits));
                write_u32(output, bits);
            }
        }
    }
    output.close();
}

std::vector<tongyue::Reference> read_bank(const std::string& path) {
    std::ifstream input(path, std::ios::binary);
    std::string magic(sizeof(kBankMagic) - 1, '\0');
    if (!input.read(magic.data(), magic.size()) || magic != kBankMagic) {
        throw std::runtime_error("Invalid reference bank signature: " + path);
    }
    const auto count = little_endian(input, 4);
    if (count == 0 || count > kBankMaxReferences) { throw std::runtime_error("Invalid reference bank size"); }
    std::vector<tongyue::Reference> refs;
    refs.reserve(count);
    std::size_t total = 0;
    for (std::uint32_t i = 0; i < count; ++i) {
        const auto length = little_endian(input, 4);
        if (length == 0 || length > 64) { throw std::runtime_error("Invalid bank reference ID length"); }
        std::string id(length, '\0');
        if (!input.read(id.data(), length) || !valid_id(id)) { throw std::runtime_error("Invalid bank reference ID"); }
        const auto frames = little_endian(input, 4);
        total += frames;
        if (frames < tongyue::kMinReferenceFrames || frames > tongyue::kMaxReferenceFrames || total > kBankMaxFrames) {
            throw std::runtime_error("Invalid bank frame count");
        }
        tongyue::Reference ref{std::move(id), std::vector<tongyue::Feature>(frames)};
        for (auto& frame : ref.frames) {
            for (float& value : frame) {
                const std::uint32_t bits = little_endian(input, 4);
                std::memcpy(&value, &bits, sizeof(value));
                if (!std::isfinite(value)) { throw std::runtime_error("Invalid bank coefficient"); }
            }
        }
        refs.push_back(std::move(ref));
    }
    if (input.peek() != std::char_traits<char>::eof() || input.bad()) { throw std::runtime_error("Invalid bank length"); }
    return refs;
}

void print_events(const std::vector<tongyue::PositionEvent>& events) {
    for (const auto& event : events) {
        const double ms = event.end_sample * 1000.0 / tongyue::kSampleRate;
        std::cout << ms << '\t' << event.status << '\t' << event.segment_id << '\t';
        if (std::isfinite(event.distance)) { std::cout << event.distance; }
        std::cout << '\t';
        if (std::isfinite(event.margin)) { std::cout << event.margin; }
        std::cout << '\n';
    }
    if (!events.empty()) { std::cout.flush(); }
}

void read_pcm_stream(tongyue::AudioLocator& locator, bool endpoint_only, bool report_progress) {
#ifdef _WIN32
    if (_setmode(_fileno(stdin), _O_BINARY) < 0) { throw std::runtime_error("Cannot set stdin to binary mode"); }
#endif
    std::array<unsigned char, 640> bytes{};
    std::array<std::int16_t, 320> samples{};
    std::uint64_t input_samples = 0, reported_samples = 0;
    for (;;) {
        std::cin.read(reinterpret_cast<char*>(bytes.data()), bytes.size());
        const auto count = static_cast<std::size_t>(std::cin.gcount());
        if (count & 1) { throw std::runtime_error("Incomplete PCM16 sample"); }
        for (std::size_t i = 0; i < count / 2; ++i) {
            const int bits = bytes[i * 2] | (bytes[i * 2 + 1] << 8);
            samples[i] = static_cast<std::int16_t>(bits < 32768 ? bits : bits - 65536);
        }
        print_events(locator.feed(samples.data(), count / 2));
        input_samples += count / 2;
        if (report_progress && input_samples - reported_samples >= tongyue::kSampleRate) {
            std::cout << "pcm\t" << input_samples << '\n' << std::flush;
            reported_samples = input_samples;
        }
        if (std::cin.eof()) { break; }
        if (!std::cin) { throw std::runtime_error("PCM input read failed"); }
    }
    if (!endpoint_only) { print_events(locator.finish()); }
}

} // namespace

int main(int argc, char** argv) {
    if (argc == 2 && std::string(argv[1]) == "--version") {
        std::cout << "tongyue-locate 3\n";
        return 0;
    }
    if (argc < 3) {
        std::cerr << "Usage: locate_wav INPUT.wav|- [--start=SEGMENT_ID|--endpoint-only|--profile-features] [--stream-progress] (--bank=FILE | ID=REFERENCE.wav [...])\n"
                  << "       locate_wav --compile-references OUTPUT ID=REFERENCE.wav [...]\n"
                  << "Use - for 16 kHz mono little-endian PCM16 on stdin.\n";
        return 2;
    }
    try {
        const auto prep_started = std::chrono::steady_clock::now();
        std::vector<tongyue::Reference> bank;
        if (std::string(argv[1]) == "--compile-references") {
            if (argc < 4 || argc - 3 > static_cast<int>(kBankMaxReferences)) { throw std::runtime_error("Invalid reference count"); }
            for (int i = 3; i < argc; ++i) { bank.push_back(from_wav(argv[i])); }
            write_bank(argv[2], bank);
            return 0;
        }
        std::string start_segment;
        bool endpoint_only = false;
        bool profile_features = false;
        bool report_progress = false;
        for (int i = 2; i < argc; ++i) {
            const std::string argument = argv[i];
            if (argument == "--stream-progress" && std::string(argv[1]) == "-") {
                report_progress = true;
                continue;
            }
            if (i == 2 && argument.compare(0, 8, "--start=") == 0) {
                start_segment = argument.substr(8);
                if (start_segment.empty()) { throw std::runtime_error("Empty start segment"); }
                continue;
            }
            if (i == 2 && argument == "--endpoint-only" && std::string(argv[1]) == "-") {
                endpoint_only = true;
                continue;
            }
            if (i == 2 && argument == "--profile-features" && std::string(argv[1]) != "-") {
                profile_features = true;
                continue;
            }
            if (argument.compare(0, 7, "--bank=") == 0) {
                if (!bank.empty() || i != argc - 1) { throw std::runtime_error("Use one bank or WAV references"); }
                bank = read_bank(argument.substr(7));
            } else {
                bank.push_back(from_wav(argument));
            }
        }
        tongyue::AudioLocator locator(std::move(bank));
        if (!start_segment.empty() && !locator.select_segment(start_segment)) {
            throw std::runtime_error("Unknown start segment: " + start_segment);
        }
        const auto prepared = std::chrono::steady_clock::now();
        std::cout << "endMs\tstatus\tsegmentId\tdistance\tmargin\n" << std::flush;
        if (std::string(argv[1]) == "-") {
            read_pcm_stream(locator, endpoint_only, report_progress);
            return 0;
        }
        WavInput input(argv[1]);
        const double seconds = static_cast<double>(input.remaining()) / tongyue::kSampleRate;
        if (profile_features) { locator.enable_feature_profile(); }
        std::array<std::int16_t, 320> samples{};
        const auto start = std::chrono::steady_clock::now();
        while (const auto count = input.read(samples.data(), samples.size())) {
            print_events(locator.feed(samples.data(), count));
        }
        print_events(locator.finish());
        const double elapsed = std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
        std::cerr << "Reference preparation ms: "
                  << std::chrono::duration<double, std::milli>(prepared - prep_started).count() << '\n';
        std::cerr << "Audio seconds: " << seconds << ", processing seconds: " << elapsed
                  << ", desktop RTF: " << elapsed / seconds << '\n';
        if (profile_features) {
            const auto& profile = locator.feature_profile();
            const auto expected_windows = profile.input_samples < tongyue::kWindowSamples ? 0 :
                1 + (profile.input_samples - tongyue::kWindowSamples) / tongyue::kHopSamples;
            std::cerr << "Feature profile: " << profile.input_samples << ' ' << expected_windows << ' '
                      << profile.analysis_windows << ' ' << profile.mfcc_windows << ' '
                      << profile.vad_ns << ' ' << profile.mfcc_ns << '\n';
        }
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
