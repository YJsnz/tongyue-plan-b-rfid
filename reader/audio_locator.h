#ifndef TONGYUE_AUDIO_LOCATOR_H
#define TONGYUE_AUDIO_LOCATOR_H

#include <array>
#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace tongyue {

constexpr int kSampleRate = 16000;
constexpr int kWindowSamples = 400;
constexpr int kHopSamples = 160;
constexpr std::size_t kMinReferenceFrames = 24;
constexpr std::size_t kMaxReferenceFrames = 800;
using Feature = std::array<float, 12>;

struct Reference {
    std::string segment_id;
    std::vector<Feature> frames;
};

Reference make_reference(std::string id, const std::vector<std::int16_t>& pcm);

struct PositionEvent {
    std::string status;
    std::string segment_id;
    double distance;
    double margin;
    std::uint64_t end_sample;
};

struct FeatureProfile {
    std::uint64_t input_samples = 0;
    std::uint64_t analysis_windows = 0;
    std::uint64_t mfcc_windows = 0;
    std::uint64_t vad_ns = 0;
    std::uint64_t mfcc_ns = 0;
};

class AudioLocator {
public:
    explicit AudioLocator(std::vector<Reference> references, double max_distance = 7.0, double min_margin = 1.0);
    std::vector<PositionEvent> feed(const std::int16_t* pcm, std::size_t count);
    std::vector<PositionEvent> finish();
    bool select_segment(const std::string& segment_id);
    void enable_feature_profile() { profile_enabled_ = true; }
    const FeatureProfile& feature_profile() const { return profile_; }
    // Drop partial speech before device playback or a question takes over the microphone.
    void reset_input();

private:
    std::vector<Reference> references_;
    std::vector<Feature> utterance_;
    std::array<std::int16_t, kWindowSamples> window_{};
    std::size_t buffered_ = 0;
    std::size_t quiet_frames_ = 0;
    bool too_long_ = false;
    int position_ = -1;
    std::uint64_t samples_ = 0;
    double max_distance_;
    double min_margin_;
    bool profile_enabled_ = false;
    FeatureProfile profile_;

    void endpoint(std::vector<PositionEvent>& events);
};

} // namespace tongyue

#endif
