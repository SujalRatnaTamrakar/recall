# One stream, different workspaces

Implemented October 31, 2026. Moderator, Campaign and Editor are local review
workspaces alongside Studio, Viewer and Local replay. They use the actual
transcript, visual observations, story memory and answer pipeline. They do not
generate sample analytics or simulate external moderation actions.

## What the research changed

| Perspective | Need supported by the references | Recall workflow |
| --- | --- | --- |
| Moderator | Twitch's Mod View brings video, chat and review tools together. Research on Twitch moderation found that appropriate surrounding context helps interpretation. | Search the broadcast, watch preceding and following footage, save a context item, and record a human review status. |
| Campaign team / sponsor manager | Twitch provides sponsorship opportunities and campaign setup. A creator and brand still need to inspect what was actually delivered. | Enter a campaign brief, search for each talking point, save source moments and manually confirm findings. |
| Editor / producer | Twitch supports markers, highlights, clip trimming and sharing. Finding a useful story around a timestamp remains a useful retrieval task. | Search for setup, change or payoff; build a shortlist; open a bounded local clip for trimming and download. |

Primary references:

- [Twitch Mod View](https://help.twitch.tv/s/article/mod-view) describes the
  existing moderation workspace. Recall's contribution here is broadcast
  context retrieval; it is not a replacement for Twitch's chat controls.
- [Analyzing Norm Violations in Live-Stream Chat, EMNLP 2023](https://aclanthology.org/2023.emnlp-main.55/)
  studies the role of context in moderation. It does not validate Recall or
  justify automated sanctions.
- [Twitch sponsorship opportunities](https://blog.twitch.tv/en/2025/02/25/expanding-your-sponsorship-opportunities-on-twitch/)
  describes creator profiles and brand connections. Our campaign proof board is
  a product inference from that workflow, not an integration with its dashboard.
- [Twitch branded content guidance](https://help.twitch.tv/s/article/branded-content-policy)
  describes its disclosure tool. A retrieved spoken disclosure is an observation,
  not a compliance determination; Recall cannot inspect the state of Twitch's
  disclosure setting from a local recording.
- [Twitch clip editing and sharing](https://help.twitch.tv/s/article/how-to-use-clips)
  and [highlights and stream markers](https://help.twitch.tv/s/article/creating-highlights-and-stream-markers)
  inform the editor workflow. Recall exports local files rather than publishing
  Twitch clips.

The general opportunity is reviewing **specific source moments**, not inferring
audience behavior from a video. Sponsor reporting is therefore a better first
fit than ad bidding, attribution, or audience targeting.

## Try each view

1. In Studio, load a recording and configure Stream context if you know the names.
2. Open Viewer and start replay. Use one source player at a time.
3. Select a new workspace in the navigation. It opens separately; keep the source
   tab playing while you work. All questions use the memory available when asked.
4. Ask a question, choose **Watch context**, and inspect the separate preview.
   It starts up to 15 seconds before the source match and stops within a
   one-minute range, bounded by the available processing time.
5. Save the source moment, add your notes, select its review status and press
   **Save review**. Use **Export notes** to download the saved board as JSON.

### Moderator · `/?view=moderator`

Ask “What was said in the last minute?” or search for the phrase being discussed.
Watch the nearby transcript and video before recording **Reviewed** or **Follow up**.
No ban, warning, deletion, viewer profiling or chat moderation is performed.
Ordinary viewer chat is not part of the content index.

### Campaign · `/?view=campaign`

Enter a sponsor or campaign name, brand terms and deliverables (one per line).
Save the brief. Select a deliverable to prepare a search; edit it if needed and
press **Ask Recall**. Save a matching moment, watch it and choose **Confirmed by
reviewer** when appropriate. The checklist reflects your saved confirmations.

Useful questions include “What did they say about the bottle's insulation?” and
“When did they explain that this segment was sponsored?” Visual descriptions may
miss a small logo or brief placement. A search miss means unobserved or unprocessed,
not proof that the deliverable was absent. The board's counts are saved review
items, not every brand appearance, exposure duration, impressions or conversions.

### Editor · `/?view=editor`

Ask about a change, earlier plan, failed attempt or story summary. Save candidate
moments, add editing notes and mark **Selected for edit** or **Set aside**.
In the preview, **Open clip editor** opens a new tab. Trim within the displayed
range, download MP4, or use file sharing when the browser supports it. Multiple
saved moments form a shortlist; this version does not assemble them into a reel.

## Storage and boundaries

- Answers in these three workspaces are private to the local request. They do not
  replace the shared broadcast answer or rotate the viewer's cube.
- Briefs and notes are never added to transcript search, story memory or model
  evidence. Campaign requirements do not become claims that something occurred.
- Boards are saved atomically in `.tubelive/runs/<run-id>/reviews-<role>.json`
  when using the normal launcher. A page refresh restores the board. Restarting
  the replay creates a new board and preserves previous run files on disk.
- Two tabs editing the same board use a revision check. A stale save is rejected
  rather than silently overwriting newer work. Export saved notes before starting
  a new replay if you want a convenient copy.
- Source IDs are checked against completed source windows. Missing or future IDs
  cannot be saved or used to create a context clip. Clip links expire on restart.
- These perspectives share one local session and have no account permissions or
  remote collaboration. Human notes can contain errors; generated answers can too.

Other promising extensions are education (questions linked to explanations),
event production (announcements and schedule changes), and accessibility review
(checking captions against footage). Those can reuse the current search and
review pattern; dedicated views are deferred until their distinct workflow is clear.

## Verification

The backend suite passed 192 tests after this implementation. New HTTP checks
exercise persisted boards, independent roles, concurrent-edit revisions, source
window cutoffs, replay restart, private answers, and real local MP4 export.
The production interface builds successfully. The existing studio/viewer browser
suite and the new three-role browser suite pass. The latter covers search,
save/reload, human review status, notes export, source preview, clip editing,
conflicting edits, mobile overflow and run changes. Its recording and answer
fixtures are explicitly synthetic; it verifies interface behavior, not model
accuracy or campaign detection rates.
