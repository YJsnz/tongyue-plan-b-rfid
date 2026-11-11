import { PositionReview, ReaderBook, ReaderCache, ReaderHistory, ReadingSession } from './RfidReader';

export interface ReadingWeek {
  sessionCount: number;
  bookCount: number;
  activeDays: number;
  durationMs: number;
  dailyMinutes: number[];
  chartMaximum: number;
  reviewCount: number;
  mismatchCount: number;
  confirmedCount: number;
  needsReviewCount: number;
}

export interface ReadingSummary {
  todayCount: number;
  streakDays: number;
  undatedCount: number;
  latestTitle: string;
  latestSegment: string;
  latestDurationMs: number;
  currentWeek: ReadingWeek;
  previousWeek: ReadingWeek;
}

function dayStart(timestamp: number): Date {
  const date = new Date(timestamp);
  date.setHours(0, 0, 0, 0);
  return date;
}

function shiftDay(date: Date, offset: number): Date {
  const result = new Date(date.getTime());
  result.setDate(result.getDate() + offset);
  return result;
}

export function confirmedEndTime(session: ReadingSession, now: number): number | undefined {
  const end = session.endedAtMs;
  if (typeof end !== 'number' || !Number.isFinite(end) || end < Date.UTC(2020, 0, 1) || end > now + 300000) {
    return undefined;
  }
  return Math.min(end, now);
}

function week(sessions: ReadingSession[], reviews: PositionReview[], deviceId: string,
  monday: Date, now: number): ReadingWeek {
  const boundaries: number[] = [];
  for (let day = 0; day <= 7; day++) { boundaries.push(shiftDay(monday, day).getTime()); }
  const dailyMinutes: number[] = [0, 0, 0, 0, 0, 0, 0];
  const books = new Set<string>();
  const days = new Set<number>();
  let sessionCount = 0;
  let durationMs = 0;
  const sessionsById = new Map<string, ReadingSession>();
  for (const session of sessions) {
    sessionsById.set(session.sessionId, session);
    const end = confirmedEndTime(session, now);
    if (end === undefined || end < boundaries[0] || end >= boundaries[7]) { continue; }
    for (let day = 0; day < 7; day++) {
      if (end >= boundaries[day + 1]) { continue; }
      days.add(day);
      dailyMinutes[day] += session.durationMs / 60000;
      break;
    }
    books.add(session.bookId);
    sessionCount++;
    durationMs += session.durationMs;
  }
  let reviewCount = 0;
  let mismatchCount = 0;
  let confirmedCount = 0;
  let needsReviewCount = 0;
  const seenReviews = new Set<string>();
  for (const review of reviews) {
    if (!review || review.deviceId !== deviceId || !['needs_review', 'position_mismatch', 'confirmed'].includes(review.status)) {
      continue;
    }
    const session = sessionsById.get(review.sessionId);
    if (!session || typeof review.eventId !== 'string' ||
      !session.trajectory?.some((entry) => entry.eventId === review.eventId)) { continue; }
    const end = confirmedEndTime(session, now);
    if (end === undefined || end < boundaries[0] || end >= boundaries[7]) { continue; }
    const key = review.sessionId + '/' + review.eventId;
    if (seenReviews.has(key)) { continue; }
    seenReviews.add(key);
    reviewCount++;
    if (review.status === 'position_mismatch') { mismatchCount++; }
    else if (review.status === 'confirmed') { confirmedCount++; }
    else { needsReviewCount++; }
  }
  const largestDay = dailyMinutes.reduce((largest: number, minutes: number) => Math.max(largest, minutes), 0);
  return { sessionCount: sessionCount, bookCount: books.size, activeDays: days.size, durationMs: durationMs,
    dailyMinutes: dailyMinutes, chartMaximum: Math.max(10, Math.ceil(largestDay / 10) * 10),
    reviewCount: reviewCount, mismatchCount: mismatchCount, confirmedCount: confirmedCount,
    needsReviewCount: needsReviewCount };
}

export function summarizeReading(cache: ReaderCache, now: number): ReadingSummary {
  const history = cache.histories.find((item: ReaderHistory) => item.deviceId === cache.currentDevice);
  const sessions: ReadingSession[] = history ? history.sessions : [];
  const today = dayStart(now);
  const monday = shiftDay(today, -((today.getDay() + 6) % 7));
  let todayCount = 0;
  let undatedCount = 0;
  const readingDays = new Set<number>();
  for (const session of sessions) {
    const end = confirmedEndTime(session, now);
    if (end === undefined) {
      undatedCount++;
      continue;
    }
    const date = dayStart(end).getTime();
    readingDays.add(date);
    if (date === today.getTime()) { todayCount++; }
  }
  let streakDays = 0;
  const mostRecent = todayCount > 0 ? today : shiftDay(today, -1);
  while (readingDays.has(shiftDay(mostRecent, -streakDays).getTime())) { streakDays++; }
  const latest = sessions[sessions.length - 1];
  const book = latest ? cache.books?.find((item: ReaderBook) => item.bookId === latest.bookId) : undefined;
  return { todayCount: todayCount, streakDays: streakDays, undatedCount: undatedCount,
    latestTitle: latest ? book?.title ?? latest.bookId : '', latestSegment: latest?.lastSegmentId ?? '',
    latestDurationMs: latest?.durationMs ?? 0,
    currentWeek: week(sessions, cache.positionReviews ?? [], cache.currentDevice, monday, now),
    previousWeek: week(sessions, cache.positionReviews ?? [], cache.currentDevice, shiftDay(monday, -7), now) };
}

export function formatReadingDuration(milliseconds: number): string {
  if (milliseconds === 0) { return '0 分钟'; }
  if (milliseconds < 60000) { return '<1 分钟'; }
  return Math.round(milliseconds / 60000) + ' 分钟';
}
