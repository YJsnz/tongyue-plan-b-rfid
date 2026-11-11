#include "audio_locator.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <complex>
#include <limits>
#include <stdexcept>
#include <utility>

namespace tongyue {
namespace {

constexpr int kFft = 512;
constexpr auto kWindow = kWindowSamples;
constexpr auto kHop = kHopSamples;
constexpr int kFilters = 24;
constexpr std::size_t kEndSilence = 65;
constexpr auto kMinFrames = kMinReferenceFrames;
constexpr auto kMaxFrames = kMaxReferenceFrames;
constexpr double kPi = 3.14159265358979323846;
constexpr double kInf = std::numeric_limits<double>::infinity();

bool voiced(const std::int16_t* frame) {
    double energy = 0;
    for (int i = 0; i < kWindow; ++i) {
        energy += static_cast<double>(frame[i]) * frame[i];
    }
    return energy > kWindow * 240.0 * 240.0;
}

struct MfccTables {
    std::array<double, kWindow> window{};
    std::array<std::complex<double>, kFft / 2> twiddles{};
    std::array<int, kFilters + 2> bins{};
    std::array<std::array<double, kFilters>, 12> dct{};

    MfccTables() {
        for (int i = 0; i < kWindow; ++i) {
            window[i] = 0.54 - 0.46 * std::cos(2 * kPi * i / (kWindow - 1));
        }
        for (int i = 0; i < kFft / 2; ++i) {
            twiddles[i] = std::polar(1.0, -2 * kPi * i / kFft);
        }
        const auto mel = [](double hz) { return 2595 * std::log10(1 + hz / 700); };
        for (int i = 0; i < kFilters + 2; ++i) {
            const double point = mel(80) + (mel(7600) - mel(80)) * i / (kFilters + 1);
            const double hz = 700 * (std::pow(10, point / 2595) - 1);
            bins[i] = static_cast<int>((kFft + 1) * hz / kSampleRate);
        }
        for (std::size_t i = 0; i < dct.size(); ++i) {
            for (int j = 0; j < kFilters; ++j) {
                dct[i][j] = std::sqrt(2.0 / kFilters) * std::cos(kPi * (i + 1) * (j + 0.5) / kFilters);
            }
        }
    }
};

Feature mfcc(const std::int16_t* frame) {
    static const MfccTables tables;
    std::array<std::complex<double>, kFft> spectrum{};
    for (int i = 0; i < kWindow; ++i) {
        const double sample = frame[i] - (i == 0 ? 0.0 : 0.97 * frame[i - 1]);
        spectrum[i] = sample * tables.window[i] / 32768.0;
    }
    for (int i = 1, j = 0; i < kFft; ++i) {
        int bit = kFft >> 1;
        for (; j & bit; bit >>= 1) { j ^= bit; }
        j ^= bit;
        if (i < j) { std::swap(spectrum[i], spectrum[j]); }
    }
    for (int length = 2; length <= kFft; length *= 2) {
        const int half = length / 2;
        for (int offset = 0; offset < kFft; offset += length) {
            for (int j = 0; j < half; ++j) {
                const auto a = spectrum[offset + j];
                const auto b = spectrum[offset + j + half] * tables.twiddles[j * (kFft / length)];
                spectrum[offset + j] = a + b;
                spectrum[offset + j + half] = a - b;
            }
        }
    }
    std::array<double, kFft / 2 + 1> power{};
    for (std::size_t i = 0; i < power.size(); ++i) { power[i] = std::norm(spectrum[i]); }
    std::array<double, kFilters> mel{};
    for (int i = 0; i < kFilters; ++i) {
        const int low = tables.bins[i], mid = tables.bins[i + 1], high = tables.bins[i + 2];
        double sum = 0;
        for (int j = low; j < mid; ++j) { sum += power[j] * (j - low) / (mid - low); }
        for (int j = mid; j < high; ++j) { sum += power[j] * (high - j) / (high - mid); }
        mel[i] = std::log(std::max(sum, 1e-9));
    }
    Feature result{};
    for (std::size_t i = 0; i < result.size(); ++i) {
        double sum = 0;
        for (int j = 0; j < kFilters; ++j) { sum += mel[j] * tables.dct[i][j]; }
        result[i] = static_cast<float>(sum);
    }
    return result;
}

struct Cell {
    double cost = kInf;
    int steps = 0;
};

std::pair<int, int> alignment_window(int row, int query_size, int reference_size) {
    const int radius = std::max(8, reference_size / 5);
    const int center = (row * reference_size + query_size / 2) / query_size;
    return {std::max(1, center - radius), std::min(reference_size, center + radius)};
}

// Each path must visit every query row. Distance to the box around the reachable
// reference frames bounds that row's cost. Divide by the longest possible path
// to bound the path-average score used by dtw().
double score_lower_bound(const std::vector<Feature>& query, const std::vector<Feature>& reference) {
    const int n = static_cast<int>(query.size()), m = static_cast<int>(reference.size());
    if (n * 2 < m || m * 2 < n) { return kInf; }
    std::array<double, kMaxFrames> squared{};
    for (std::size_t k = 0; k < query.front().size(); ++k) {
        std::array<int, kMaxFrames> minima, maxima;
        int min_head = 0, min_tail = 0, max_head = 0, max_tail = 0, added = 0;
        for (int row = 1; row <= n; ++row) {
            const auto [first, last] = alignment_window(row, n, m);
            while (added < last) {
                const float value = reference[added][k];
                while (min_tail > min_head && reference[minima[min_tail - 1]][k] >= value) { --min_tail; }
                while (max_tail > max_head && reference[maxima[max_tail - 1]][k] <= value) { --max_tail; }
                minima[min_tail++] = added;
                maxima[max_tail++] = added++;
            }
            while (minima[min_head] < first - 1) { ++min_head; }
            while (maxima[max_head] < first - 1) { ++max_head; }
            const double value = query[row - 1][k];
            const double low = reference[minima[min_head]][k], high = reference[maxima[max_head]][k];
            const double delta = value - std::clamp(value, low, high);
            squared[row - 1] += delta * delta;
        }
    }
    double total = 0;
    for (int i = 0; i < n; ++i) { total += std::sqrt(squared[i]); }
    return total / (n + m - 1);
}

double dtw(const std::vector<Feature>& query, const std::vector<Feature>& reference) {
    const int n = static_cast<int>(query.size()), m = static_cast<int>(reference.size());
    if (n * 2 < m || m * 2 < n) { return kInf; }
    std::vector<Cell> prev(m + 1), row(m + 1);
    prev[0] = {0, 0};
    for (int i = 1; i <= n; ++i) {
        std::fill(row.begin(), row.end(), Cell{});
        const auto [first, last] = alignment_window(i, n, m);
        for (int j = first; j <= last; ++j) {
            Cell best = prev[j - 1];
            if (prev[j].cost < best.cost) { best = prev[j]; }
            if (row[j - 1].cost < best.cost) { best = row[j - 1]; }
            double squared = 0;
            for (std::size_t k = 0; k < query[i - 1].size(); ++k) {
                const double delta = query[i - 1][k] - reference[j - 1][k];
                squared += delta * delta;
            }
            row[j] = {best.cost + std::sqrt(squared), best.steps + 1};
        }
        prev.swap(row);
    }
    return prev[m].steps ? prev[m].cost / prev[m].steps : kInf;
}

} // namespace

Reference make_reference(std::string id, const std::vector<std::int16_t>& pcm) {
    std::size_t first = pcm.size(), last = 0;
    for (std::size_t offset = 0; offset + kWindow <= pcm.size(); offset += kHop) {
        if (voiced(pcm.data() + offset)) {
            first = std::min(first, offset);
            last = offset;
        }
    }
    if (first == pcm.size() || (last - first) / kHop + 1 < kMinFrames ||
        (last - first) / kHop + 1 > kMaxFrames) {
        throw std::invalid_argument("Reference needs 0.24 to 8 seconds of speech: " + id);
    }
    Reference reference{std::move(id), {}};
    for (std::size_t offset = first; offset <= last; offset += kHop) {
        reference.frames.push_back(mfcc(pcm.data() + offset));
    }
    return reference;
}

AudioLocator::AudioLocator(std::vector<Reference> references, double max_distance, double min_margin)
    : references_(std::move(references)), max_distance_(max_distance), min_margin_(min_margin) {
    if (references_.empty() || !std::isfinite(max_distance_) || !std::isfinite(min_margin_) ||
        max_distance_ <= 0 || min_margin_ < 0) {
        throw std::invalid_argument("Invalid reference bank or thresholds");
    }
    for (std::size_t i = 0; i < references_.size(); ++i) {
        const auto& ref = references_[i];
        if (ref.segment_id.empty() || ref.frames.size() < kMinFrames || ref.frames.size() > kMaxFrames) {
            throw std::invalid_argument("Invalid reference segment");
        }
        for (std::size_t j = 0; j < i; ++j) {
            if (references_[j].segment_id == ref.segment_id) { throw std::invalid_argument("Duplicate segment ID"); }
        }
    }
    utterance_.reserve(kMaxFrames + kEndSilence);
}

std::vector<PositionEvent> AudioLocator::feed(const std::int16_t* pcm, std::size_t count) {
    using Clock = std::chrono::steady_clock;
    using Nanoseconds = std::chrono::nanoseconds;
    std::vector<PositionEvent> events;
    if (profile_enabled_) { profile_.input_samples += count; }
    while (count > 0) {
        const std::size_t take = std::min(count, window_.size() - buffered_);
        std::copy_n(pcm, take, window_.begin() + buffered_);
        pcm += take;
        count -= take;
        buffered_ += take;
        samples_ += take;
        if (buffered_ != window_.size()) { continue; }
        const auto vad_start = profile_enabled_ ? Clock::now() : Clock::time_point{};
        const bool speech = voiced(window_.data());
        if (profile_enabled_) {
            ++profile_.analysis_windows;
            profile_.vad_ns += std::chrono::duration_cast<Nanoseconds>(Clock::now() - vad_start).count();
        }
        if (speech || !utterance_.empty() || too_long_) {
            quiet_frames_ = speech ? 0 : quiet_frames_ + 1;
            if (quiet_frames_ == kEndSilence) {
                endpoint(events);
            } else if (!too_long_) {
                if (utterance_.size() == kMaxFrames + kEndSilence) {
                    too_long_ = true;
                } else {
                    const auto mfcc_start = profile_enabled_ ? Clock::now() : Clock::time_point{};
                    const auto feature = mfcc(window_.data());
                    if (profile_enabled_) {
                        ++profile_.mfcc_windows;
                        profile_.mfcc_ns += std::chrono::duration_cast<Nanoseconds>(Clock::now() - mfcc_start).count();
                    }
                    utterance_.push_back(feature);
                }
            }
        }
        std::move(window_.begin() + kHop, window_.end(), window_.begin());
        buffered_ -= kHop;
    }
    return events;
}

std::vector<PositionEvent> AudioLocator::finish() {
    std::vector<PositionEvent> events;
    endpoint(events);
    reset_input();
    return events;
}

bool AudioLocator::select_segment(const std::string& segment_id) {
    for (std::size_t i = 0; i < references_.size(); ++i) {
        if (references_[i].segment_id == segment_id) {
            position_ = static_cast<int>(i);
            reset_input();
            return true;
        }
    }
    return false;
}

void AudioLocator::reset_input() {
    buffered_ = 0;
    utterance_.clear();
    quiet_frames_ = 0;
    too_long_ = false;
}

void AudioLocator::endpoint(std::vector<PositionEvent>& events) {
    // The final quiet frame at a VAD endpoint was not appended.
    const std::size_t trailing = quiet_frames_ == kEndSilence ? quiet_frames_ - 1 : quiet_frames_;
    if (trailing <= utterance_.size()) { utterance_.resize(utterance_.size() - trailing); }
    too_long_ = too_long_ || utterance_.size() > kMaxFrames;
    if (too_long_ || utterance_.size() >= kMinFrames) {
        int best_id = -1;
        double best = kInf, second = kInf;
        const int total = static_cast<int>(references_.size());
        const int start = position_ < 0 ? 0 : std::max(0, position_ - 1);
        const int end = position_ < 0 ? total : std::min(total, position_ + 3);
        const auto consider = [&](int index) {
            const double score = dtw(utterance_, references_[index].frames);
            if (score < best) { second = best; best = score; best_id = index; }
            else if (score < second) { second = score; }
        };
        const auto accepted = [&]() { return best_id >= 0 && best <= max_distance_ && second - best >= min_margin_; };
        if (!too_long_) {
            for (int i = start; i < end; ++i) { consider(i); }
            // A distant segment can still win or make the local result ambiguous.
            // Skip it only if its best possible score cannot change the global top two.
            for (int i = 0; i < total; ++i) {
                if (i >= start && i < end) { continue; }
                if (std::isfinite(second)) {
                    const double margin = 1e-9 * std::max(1.0, second);
                    if (score_lower_bound(utterance_, references_[i].frames) > second + margin) { continue; }
                }
                consider(i);
            }
        }
        std::string status = too_long_ ? "utterance_too_long" : "uncertain";
        if (accepted()) {
            status = best_id == position_ ? "repeated" :
                (position_ >= 0 && std::abs(best_id - position_) > 1 ? "relocated" : "matched");
            position_ = best_id;
        }
        events.push_back({status, position_ < 0 ? "" : references_[position_].segment_id, best,
                          std::isfinite(second) ? second - best : kInf, samples_});
    }
    utterance_.clear();
    quiet_frames_ = 0;
    too_long_ = false;
}

} // namespace tongyue
