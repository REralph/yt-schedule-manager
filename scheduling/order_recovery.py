"""RECOVERING THE ORIGINAL QUEUE ORDER, from evidence, without guessing.

The problem: scheduling features were once run on an incomplete picture of the channel, so the order videos are
scheduled to publish in no longer matches the order they belong in. The publish time therefore cannot be trusted as
the authority on order. This module reconstructs the order from the strongest evidence there is, and reports --
instead of hiding -- everything it cannot be sure of.

EVIDENCE, strongest first
  1. A recorded sequence number (data/queue_order.py). Once one exists nothing here is needed; this module only
     creates that first record.
  2. The ORIGINAL ADD SEQUENCE: when each video was created on YouTube (snippet.publishedAt, which for a scheduled
     video is its upload time; the upload record's own uploaded_at corroborates it). It exists for every video, is set
     once, and no scheduling feature can change it. It gives the base order.
  3. The schedule-change history (data/history.json), which records every video's time before and after each
     operation. Undoing the operations newest-first rebuilds how the schedule looked BEFORE each one ("arrangements"),
     which are checked against the base order. The current schedule is the last arrangement -- and the weakest,
     because it is the one a faulty operation may already have changed.
Never used: titles, file names, video IDs, or the order an API happens to return videos in.

WHAT HAPPENS WHERE AN ARRANGEMENT DISAGREES WITH THE UPLOAD ORDER
Every disagreeing pair of videos is looked at. It is EXPLAINED (the position is kept, the reason is stored with the
video and shown) only by one of three rules:
  * earlier arrangement agrees -- the earliest recorded arrangement containing both had them in upload order, so the
    later disagreement came from a recorded operation;
  * same-day swap -- both sit on one calendar day with the later upload in the earlier slot: the placement bug you
    reported (first upload given 10:00, second 06:00), since fixed;
  * added after the operation -- the later video was uploaded after a recorded operation had already moved the
    earlier one, so its slot was assigned while the schedule was displaced; only the upload sequence is independent.
Every pair no rule explains is a DISAGREEMENT THAT NEEDS YOU: the videos involved are shown together with both
candidate orders and the evidence, and nothing is recorded until you choose. Equal upload times are always such a
case. A video with no usable creation time blocks the recovery and is listed -- it is never given a guessed place.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
from typing import Iterable, Optional, Sequence

from youtube.models import Video

STATUS_CERTAIN = "certain"  # no recorded arrangement disagrees with this position
STATUS_RESOLVED = "resolved"  # a disagreement existed and a rule explains it (the reason is stored)
STATUS_DECIDED = "decided"  # a disagreement that YOU decided

KIND_DECISION = "decision"
KIND_TIE = "tie"

OPT_UPLOAD = "upload"
OPT_SCHEDULE = "schedule"

ISSUE_NO_CREATION_TIME = "no_creation_time"
ISSUE_CREATION_IS_SCHEDULE = "creation_time_is_schedule_time"
ISSUE_DUPLICATE_VIDEO = "duplicate_video"
ISSUE_NO_VIDEOS = "no_videos"


class RecoveryError(RuntimeError):
    pass


class RecoveryBlocked(RecoveryError):
    """There are problems that make a recovered order untrustworthy (see report.issues)."""


class RecoveryIncomplete(RecoveryError):
    """Some groups still need a decision."""

    def __init__(self, block_ids: list[str]):
        super().__init__(f"{len(block_ids)} group(s) still need a decision: {', '.join(block_ids)}")
        self.block_ids = block_ids


@dataclasses.dataclass
class Issue:
    kind: str
    message: str
    video_ids: list[str] = dataclasses.field(default_factory=list)
    blocking: bool = True


@dataclasses.dataclass
class Arrangement:
    """How the schedule looked at one moment of the recorded history, for the videos that existed then."""
    key: str
    label: str
    at: Optional[dt.datetime]
    times: dict[str, dt.datetime]


@dataclasses.dataclass
class DecisionOption:
    key: str
    label: str
    order: list[str]  # the group's members in the order this option puts them


@dataclasses.dataclass
class ConflictBlock:
    block_id: str
    kind: str
    members: list[str]  # in upload order
    options: dict[str, DecisionOption]
    reason: str
    unexplained_pairs: int
    evidence: list[str]
    recommended: str = OPT_UPLOAD  # shown as the suggestion; NEVER applied without the user's choice


@dataclasses.dataclass
class RecoveredItem:
    video_id: str
    title: str
    position: int  # 1-based
    created_at: Optional[dt.datetime]
    current_at: Optional[dt.datetime]
    status: str
    block_id: Optional[str]
    evidence: list[str]

    @property
    def evidence_text(self) -> str:
        return "; ".join(self.evidence)


@dataclasses.dataclass
class RecoveryReport:
    base_order: list[str]  # upload order
    titles: dict[str, str]
    created: dict[str, dt.datetime]
    current: dict[str, dt.datetime]
    blocks: list[ConflictBlock]
    issues: list[Issue]
    arrangements: list[Arrangement]
    notes: dict[str, list[str]] = dataclasses.field(default_factory=dict)  # video -> rules that explained a disagreement
    ignored: list[str] = dataclasses.field(default_factory=list)  # informational lines

    # ---- state ----

    @property
    def blocking_issues(self) -> list[Issue]:
        return [i for i in self.issues if i.blocking]

    def decisions_needed(self) -> list[ConflictBlock]:
        return list(self.blocks)

    def missing_choices(self, choices: Optional[dict[str, str]]) -> list[str]:
        choices = choices or {}
        return [b.block_id for b in self.blocks if choices.get(b.block_id) not in b.options]

    @property
    def explained_videos(self) -> int:
        return len([v for v in self.notes if v not in {m for b in self.blocks for m in b.members}])

    @property
    def is_clean(self) -> bool:
        """Nothing blocks and nothing needs a decision: the order can be recorded as it is."""
        return not self.blocking_issues and not self.blocks

    # ---- the final order ----

    def build(self, choices: Optional[dict[str, str]] = None) -> list[RecoveredItem]:
        """The recovered order, every group settled by `choices`. Raises RecoveryBlocked / RecoveryIncomplete
        instead of ever guessing."""
        if self.blocking_issues:
            raise RecoveryBlocked("; ".join(i.message for i in self.blocking_issues))
        choices = dict(choices or {})
        missing = self.missing_choices(choices)
        if missing:
            raise RecoveryIncomplete(missing)

        pos = {vid: i for i, vid in enumerate(self.base_order)}
        order = list(self.base_order)
        block_of: dict[str, ConflictBlock] = {}
        for b in self.blocks:
            slots = sorted(pos[m] for m in b.members)  # the group re-orders ITSELF inside the places its members already occupy
            for slot, vid in zip(slots, b.options[choices[b.block_id]].order):
                order[slot] = vid
            for m in b.members:
                block_of[m] = b

        if len(order) != len(self.base_order) or set(order) != set(self.base_order):
            raise RecoveryError("Internal check failed: the recovered order does not contain every video exactly once.")

        items: list[RecoveredItem] = []
        for n, vid in enumerate(order, start=1):
            b = block_of.get(vid)
            evidence = [f"uploaded {_fmt(self.created.get(vid))} (YouTube creation time)"]
            if b is not None:
                status = STATUS_DECIDED
                evidence.append(f"you chose: {b.options[choices[b.block_id]].label}")
            elif vid in self.notes:
                status = STATUS_RESOLVED
                evidence.extend(self.notes[vid][:2])
            else:
                status = STATUS_CERTAIN
                evidence.append("no recorded arrangement of the schedule contradicts this position")
            items.append(RecoveredItem(
                video_id=vid, title=self.titles.get(vid, ""), position=n, created_at=self.created.get(vid),
                current_at=self.current.get(vid), status=status, block_id=b.block_id if b else None, evidence=evidence,
            ))
        return items

    # ---- text for the page / clipboard ----

    def summary_lines(self, choices: Optional[dict[str, str]] = None) -> list[str]:
        n = len(self.base_order)
        in_blocks = {m for b in self.blocks for m in b.members}
        resolved = [v for v in self.notes if v not in in_blocks]
        lines = [f"{n} scheduled Short(s) in the queue."]
        lines.append(f"{n - len(in_blocks) - len(resolved)} have a position that no recorded arrangement contradicts; "
                     f"{len(resolved)} were contradicted by a recorded arrangement but the disagreement is explained (the reason is shown for each).")
        waiting = self.missing_choices(choices)
        if self.blocks:
            lines.append(f"{len(self.blocks)} group(s) ({len(in_blocks)} videos) have a disagreement nothing explains"
                         + (f" -- {len(waiting)} still need your decision." if waiting else " -- all decided."))
        for i in self.issues:
            lines.append(("PROBLEM: " if i.blocking else "Note: ") + i.message)
        lines.extend(self.ignored)
        return lines

    def to_text(self, choices: Optional[dict[str, str]] = None) -> str:
        out = ["QUEUE ORDER RECOVERY REPORT", ""]
        out.extend(self.summary_lines(choices))
        out.append("")
        out.append("Arrangements of the schedule that were checked (oldest first):")
        for a in self.arrangements:
            out.append(f"  - {a.label}: {len(a.times)} video(s)")
        for b in self.blocks:
            out.append("")
            out.append(f"GROUP {b.block_id} ({b.kind}, {len(b.members)} videos, {b.unexplained_pairs} unexplained pair(s)) -- recommended: {b.recommended}; chosen: {(choices or {}).get(b.block_id, 'NOT YET DECIDED')}")
            out.append(f"  {b.reason}")
            out.extend(f"  evidence: {e}" for e in b.evidence)
            for o in b.options.values():
                out.append(f"  option {o.key}: {o.label}")
                out.extend(f"      {self.titles.get(v, v)[:60]}" for v in o.order[:12])
                if len(o.order) > 12:
                    out.append(f"      ... and {len(o.order) - 12} more")
        if not self.blocking_issues and not self.missing_choices(choices):
            items = self.build(choices)
            out.append("")
            out.append("RECOVERED ORDER")
            for it in items:
                out.append(f"{it.position:>4}  {it.title[:56]:56}  uploaded {_fmt(it.created_at)}  now {_fmt(it.current_at)}  [{it.status}]  {it.evidence_text}")
        return "\n".join(out)


def _fmt(t: Optional[dt.datetime]) -> str:
    return t.astimezone().strftime("%d %b %Y %H:%M") if t else "?"


def _day(t: dt.datetime) -> dt.date:
    return t.astimezone().date()


def _parse(value: str) -> Optional[dt.datetime]:
    try:
        t = dt.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.astimezone()


# ---------------------------------------------------------------------------------------------------------------

def recover_order(videos: Iterable[Video], history_entries: Iterable = (), upload_entries: Iterable = ()) -> RecoveryReport:
    """`videos`: what YouTube currently has (the queue is its future-scheduled Shorts). `history_entries`:
    data/history.py HistoryEntry objects. `upload_entries`: data/upload_activity.py entries (only used to fill in a
    creation time YouTube did not return)."""
    issues: list[Issue] = []
    seen: dict[str, Video] = {}
    for v in videos:
        if not (v.is_scheduled and v.is_short):
            continue
        if v.video_id in seen:
            issues.append(Issue(ISSUE_DUPLICATE_VIDEO, f"Video {v.video_id} appears more than once in the schedule data.", [v.video_id]))
            continue
        seen[v.video_id] = v

    uploads = {e.video_id: e for e in upload_entries if getattr(e, "video_id", "")}
    created: dict[str, dt.datetime] = {}
    for vid, v in seen.items():
        t = v.published_at
        if t is None:
            e = uploads.get(vid)
            t = _parse(e.uploaded_at) if e is not None and getattr(e, "uploaded_at", "") else None
        if t is None:
            issues.append(Issue(ISSUE_NO_CREATION_TIME, f'"{v.title[:50]}" has no recorded upload time, so its place in the order cannot be recovered.', [vid]))
            continue
        if v.publish_at is not None and t == v.publish_at:
            issues.append(Issue(ISSUE_CREATION_IS_SCHEDULE, f'"{v.title[:50]}": its recorded upload time is identical to its scheduled time, so it is not independent evidence of order.', [vid]))
            continue
        created[vid] = t

    titles = {vid: v.title for vid, v in seen.items()}
    current = {vid: seen[vid].publish_at for vid in created}
    if not seen:
        issues.append(Issue(ISSUE_NO_VIDEOS, "No scheduled Shorts were found, so there is no queue to record.", blocking=True))

    entries = [e for e in history_entries if not getattr(e, "undone", False)]
    arrangements = _arrangements(current, created, entries)
    moved_at: dict[str, list[dt.datetime]] = {}
    for e in entries:
        for c in e.changes:
            moved_at.setdefault(c.video_id, []).append(e.timestamp)

    # base order = upload order; equal upload times are put in current-schedule order but always flagged as a tie
    base = sorted(created, key=lambda vid: (created[vid], current[vid]))
    pos = {vid: i for i, vid in enumerate(base)}

    unexplained: list[tuple[int, int, str]] = []  # (i, j, how) -- pairs nothing explains
    notes: dict[str, dict[str, int]] = {}  # video -> {explanation: how many pairs}
    for i in range(len(base)):
        u = base[i]
        for j in range(i + 1, len(base)):
            v = base[j]
            if created[u] == created[v]:
                unexplained.append((i, j, "equal upload times"))
                continue
            opinions: list[tuple[int, int]] = []  # (arrangement index, +1 = u before v / -1 = v before u)
            for k, a in enumerate(arrangements):
                tu, tv = a.times.get(u), a.times.get(v)
                if tu is None or tv is None or tu == tv:
                    continue
                opinions.append((k, 1 if tu < tv else -1))
            contradicting = [k for k, o in opinions if o == -1]
            if not contradicting:
                continue
            first_k, first_o = opinions[0]
            how = None
            if first_o == 1:
                how = f"{arrangements[first_k].label} had them in upload order, so the later disagreement came from a recorded operation"
            elif _day(arrangements[contradicting[0]].times[u]) == _day(arrangements[contradicting[0]].times[v]):
                how = "same-day swap (both on one day, the later upload in the earlier slot: the 10:00-before-06:00 placement bug)"
            else:
                moved = [ts for ts in moved_at.get(u, []) if ts < created[v]]
                if moved:
                    how = (f"uploaded after the {max(moved).astimezone():%d %b %Y} operation had already moved the earlier video, "
                           "so its slot was assigned while the schedule was displaced; the upload sequence is the only independent evidence")
            if how is None:
                unexplained.append((i, j, f"{arrangements[contradicting[0]].label} had them in the opposite order and nothing explains it"))
                continue
            for vid in (u, v):
                d = notes.setdefault(vid, {})
                d[how] = d.get(how, 0) + 1

    blocks = _group(base, pos, unexplained, arrangements, current)
    return RecoveryReport(
        base_order=base, titles=titles, created=created, current=current, blocks=blocks, issues=issues, arrangements=arrangements,
        notes={vid: sorted(d, key=lambda k: -d[k]) for vid, d in notes.items()},
    )


def _arrangements(current: dict[str, dt.datetime], created: dict[str, dt.datetime], entries: list) -> list[Arrangement]:
    """Undo the recorded operations newest-first. A video that was moved again by something that left no record
    (its time is not the one the operation set) has unknown earlier positions, so it is left out of the earlier
    arrangements instead of being guessed."""
    snap = dict(current)
    unknown: set[str] = set()
    built: list[Arrangement] = []
    for entry in sorted(entries, key=lambda e: e.timestamp, reverse=True):
        for c in entry.changes:
            vid = c.video_id
            if vid not in snap:
                continue
            if c.old_publish_at is not None and snap[vid] == c.new_publish_at:
                snap[vid] = c.old_publish_at
            else:
                unknown.add(vid)
        members = {vid: t for vid, t in snap.items() if vid not in unknown and created[vid] < entry.timestamp}
        label = f"the schedule before the {entry.timestamp.astimezone():%d %b %Y} operation ({entry.description[:70]})"
        built.append(Arrangement(f"before:{entry.entry_id}", label, entry.timestamp, members))
    built.reverse()
    built.append(Arrangement("now", "the current schedule", None, dict(current)))
    return built


def _group(base, pos, unexplained, arrangements, current) -> list[ConflictBlock]:
    """Videos joined by an unexplained disagreement form one group (connected components, so unrelated
    disagreements stay separate however far apart they are)."""
    if not unexplained:
        return []
    parent = {i: i for i in range(len(base))}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, j, _ in unexplained:
        parent[find(i)] = find(j)
    comps: dict[int, list[int]] = {}
    for i, j, _ in unexplained:
        for x in (i, j):
            comps.setdefault(find(x), [])
    for i in range(len(base)):
        r = find(i)
        if r in comps and (i in {a for a, _, _ in unexplained} or i in {b for _, b, _ in unexplained}):
            comps[r].append(i)
    ordered = sorted((sorted(set(ix)) for ix in comps.values()), key=lambda ix: ix[0])

    blocks: list[ConflictBlock] = []
    for n, idxs in enumerate(ordered, start=1):
        members = [base[i] for i in idxs]
        pairs = [(i, j, how) for i, j, how in unexplained if i in idxs]
        ties = [p for p in pairs if p[2] == "equal upload times"]
        ref = _reference(members, arrangements)
        sched_order = sorted(members, key=lambda vid: (ref.times.get(vid, current[vid]), pos[vid]))
        upload_option = DecisionOption(OPT_UPLOAD, "upload order (the order they were added)", list(members))
        sched_option = DecisionOption(OPT_SCHEDULE, f"schedule order ({ref.label})", sched_order)
        if len(ties) == len(pairs):
            options = {OPT_SCHEDULE: DecisionOption(OPT_SCHEDULE, "the current schedule's order (their upload times are identical)", sched_order)}
            kind, recommended = KIND_TIE, OPT_SCHEDULE
            reason = "These videos have identical upload times, so their order cannot be taken from the upload sequence."
        else:
            options = {OPT_UPLOAD: upload_option, OPT_SCHEDULE: sched_option}
            kind, recommended = KIND_DECISION, OPT_UPLOAD
            reason = f"{len(pairs)} pair(s) of these videos are in a different order in a recorded arrangement than in the upload sequence, and no rule explains it."
        blocks.append(ConflictBlock(f"G{n}", kind, members, options, reason, len(pairs), _evidence(members, arrangements), recommended))
    return blocks


def _reference(members: list[str], arrangements: list[Arrangement]) -> Arrangement:
    """The earliest arrangement that contains every member of the group (the current schedule always does)."""
    for a in arrangements:
        if all(vid in a.times for vid in members):
            return a
    return arrangements[-1]


def _evidence(members: list[str], arrangements: list[Arrangement]) -> list[str]:
    out: list[str] = []
    for a in arrangements:
        present = [vid for vid in members if vid in a.times]
        if len(present) < 2:
            continue
        agree = disagree = 0
        for x in range(len(present)):
            for y in range(x + 1, len(present)):
                tx, ty = a.times[present[x]], a.times[present[y]]
                if tx == ty:
                    continue
                if tx < ty:
                    agree += 1
                else:
                    disagree += 1
        out.append(f"{a.label}: {len(present)} of {len(members)} videos present; {agree} pair(s) in upload order, {disagree} reversed")
    return out


def registry_items(items: Sequence[RecoveredItem]) -> list[tuple[str, str, str, str]]:
    """(video_id, title, evidence, source) in order, ready for QueueOrderStore.adopt()."""
    out = []
    for it in items:
        source = "decision" if it.status == STATUS_DECIDED else "recovered"
        out.append((it.video_id, it.title, f"{it.status}: {it.evidence_text}"[:400], source))
    return out
