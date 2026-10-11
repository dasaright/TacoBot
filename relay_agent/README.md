# TacoBot

The relay lets the Railway-hosted bot play YouTube audio through a volunteer's
Windows internet connection. The Discord bot token, database, permissions,
queues, and commands remain on Railway.

## Install

Repository: https://github.com/dasaright/TacoBot

If upgrading from a version released before the repository rename, download
this version manually once. The older updater rejects download links containing
the new repository name. Close the old relay, extract this ZIP into the same
folder, and run `TacoBot.exe`. Keep `relay-config.json` and your clip folders.
You may remove the old `RunsformingRelay.exe` after confirming TacoBot opens.
Future updates use the TacoBot repository automatically.

1. Download `TacoBot-Windows.zip` from a `relay-` GitHub release.
2. Extract the complete ZIP. Keep `TacoBot.exe` and `README.md` in
   the main folder, with `ffmpeg.exe` and `yt-dlp.exe` inside `Files`.
3. Run `TacoBot.exe` and enter your relay
   token sent privately by the Discord bot after you use `!token`.
4. Leave the program open while you are willing to relay audio.

Ask the bot owner for a relay token before completing setup.

The program creates `relay-config.json` next to the executable. That file
contains a secret relay token and must not be shared.

## Bot-owner Railway setup

1. Generate a public Railway domain for the existing bot service.
2. Add `AUDIO_RELAY_TOKENS` as a JSON object. Each relay PC gets a unique,
   revocable token, for example:

   ```json
   {"Taco PC":"long-random-token","Volunteer PC":"another-random-token"}
   ```

3. Give each volunteer the WebSocket URL
   `wss://YOUR-DOMAIN.up.railway.app/relay` and only their own token.
4. Redeploy after changing the token list. The `/health` endpoint reports the
   number of connected relays but never exposes their names or tokens.

The relay makes an outbound encrypted WebSocket connection. Volunteers do not
need to open router ports, configure port forwarding, or run the Discord bot.

## Stable update model

The relay protocol is versioned independently of the main bot. The executable
only supports four stable operations: authenticate, inspect a YouTube URL,
stream fixed-format PCM audio, and cancel a stream. Changes to Discord
commands, permissions, queues, forms, personas, or server configuration do not
require a relay update.

`yt-dlp.exe` checks for its own updates whenever the relay starts. FFmpeg and
the relay executable only need replacement when the protocol or their own
runtime requirements change.

## Optional cookies

The relay normally uses the PC's residential connection without an account.
If a video requires authentication, set `cookies_file` in `relay-config.json`
to a Netscape-format cookie file stored on that same PC. Never share a cookie
file with the bot owner, Railway, or another relay host.

## Soundboard playback

The relay opens a desktop window. Choose a folder to scan its audio files; the
folder is remembered in relay-config.json. Empty or undecodable files are omitted.
Use Refresh files after adding or removing files.

Join a Discord voice channel; your personal token finds it automatically. A single click
on an audio file plays it immediately as a soundboard clip over the current music.
The music and queue are preserved. Multiple clips can overlap (up to eight).
Stop clips stops only clips. `y!pause` pauses music; `y!resume` or `y!play`
without a URL resumes it. Clips can play while music is paused. `y!stop` stops
all audio and clears the music queue. Audio files remain on the PC: only decoded audio is
sent to Railway. Local controls require the approved Owner PC relay credential;
other volunteer credentials continue to support YouTube requests.

Bot/command changes continue to work without replacing this executable. This
MP3 feature requires one executable upgrade from the original console version.

## YouTube playlists and automatic disconnect

`y!play <video URL>` queues only the linked song, even when the URL includes
playlist information. `y!playlist <playlist URL>` appends the first 50 entries
in playlist order (unavailable entries are skipped). Install this version of
the relay to enable playlist inspection. Local MP3 and single-video playback
remain compatible with older relays.

Audio commands connect or move the bot to the requesting user's voice channel.
The bot leaves when no human listeners remain or five minutes pass without
another audio command or local playback request, including during playback.

## Certificate verification

The relay bundles Mozilla's public certificate roots through certifi and uses
that bundle explicitly for encrypted connections. Certificate expiration and
hostname checks remain enabled. This avoids selecting stale certificates from
the Windows certificate store. Keep the PC clock synchronized. If an error
persists while Chrome connects, check Chrome's certificate issuer and expiration
date to identify an antivirus or network HTTPS inspection certificate.

Soundboard mixing and pause/resume require this updated executable. Keep your
existing relay-config.json when replacing the program.

## Desktop controls

The YouTube Music tab shows music and the queue, with the current song highlighted. Pause/Play
controls music only. Next advances the queue; Previous returns to the most
recently played song and puts the interrupted song at the front of the queue.
The Soundboard tab plays clips only when the filename is clicked.
Stop all clips stops every active clip while preserving music. Clips do not
post Now playing messages in Discord. The queue refreshes every two seconds.

Music uses a half-second jitter buffer and absolute 20 ms frame deadlines
to avoid accumulating Windows scheduling delays. Soundboard clips remain
immediate; music re-buffers if its network stream stalls.

## Audio library and drag-and-drop

Supported soundboard formats: MP3, OGG/OGA, Opus, WAV, FLAC, M4A, AAC, WMA,
and AIFF/AIF. Files must contain decodable audio. The list hides extensions.
Choose a folder, then drop one or more audio files anywhere in the window.
The relay copies them into the selected folder and refreshes the library;
originals are preserved. Existing names are preserved with numbered copies.
Files already in the selected folder and invalid files are skipped.

## Relay updates

Update automatically is enabled by default. The program checks published
`relay-` releases in dasaright/TacoBot on startup and every six hours.
Use Check for updates for a manual check; uncheck Update automatically to
turn automatic updates off. The preference is saved in relay-config.json.

Updates download in the background and check the release SHA-256 checksum.
Installation waits until music, its queue, and clips are finished, followed
by 30 seconds idle. The relay then restarts itself. Settings, tokens, cookies,
and soundboard files are not replaced. Previous program files are backed up
while installing; a failed copy restores them. The last installer result is
stored at Files/update-status.txt. If a check or download fails, playback
continues and the relay retries at its next scheduled check.

For this initial upgrade, close the old relay and extract the new bundle
into its folder, preserving relay-config.json. Legacy root-level FFmpeg and
yt-dlp files can be removed after Files contains their replacements. Future
relay updates use GitHub releases automatically; ordinary bot updates do not
trigger a new executable build.

## Shared clips

The Soundboard tab lists your chosen local folder and the separate `sharedclips` folder beside the executable. Name sorts filenames; Shared toggles checked-first / unchecked-first. Earlier sort choices remain as tie-breakers. Sort priority and direction are saved in `relay-config.json` and survive refreshes, restarts, and updates. Click a filename to play. **Sync clips** uploads new files from `sharedclips` and downloads missing shared files. Local files are never uploaded. Put files into `sharedclips` to share them. Shared files are public in this repository. Sync adds files only; rename conflicts and keep clips under 20 MB. Updates preserve both folders.

Bot administrator: configure `SHARED_CLIPS_GITHUB_TOKEN` on Railway with a fine-grained GitHub token restricted to `dasaright/TacoBot`, Contents read/write. Approved relay tokens authorize sync. The GitHub token stays on the bot and is never distributed in the executable.

## Personal relay tokens and updates

Use `!token` in Discord (server or DM) to receive a personal token privately. Each use replaces the old token and disconnects relays using it. Setup asks only for this token; the Railway URL is fixed automatically and the bot supplies your Discord username. Invalid/replaced tokens prompt for a new token. Join voice and click a clip to have the bot connect automatically to your channel. You can control audio only from your own voice channel.

Automatic updates wait for actual audio running on this PC, not remote queued music. Manual checks install when downloaded; **Install update now** installs a pending update immediately, interrupting this relay's audio. Both clip folders and configuration are preserved.

The song pane retains previous songs for the current session. Click any song to switch to it and continue from there; the current song remains highlighted. Stop/leave clears the session playlist. Install update now is disabled until an update is downloaded. The bot remains in voice while at least one connected personal relay's Discord user is present in that channel; otherwise the usual five-minute idle timeout applies.

Update failures are recorded in `Files/update-launch.log` and `Files/update-install.log`. The relay only closes after the independent installer confirms it has started. Restart uses a fresh executable runtime, and renamed relay executables are updated in place. A failed update delays automatic retry until the next scheduled check; manual retry remains available.

With the relay window active, press Ctrl+V to add the first YouTube link found in the clipboard to Discord's music queue. The checkbox below the music list switches pasted links to playlist mode (up to 50 songs), matching `y!playlist`. It starts unchecked and is remembered in your local config. Your personal token identifies you; join voice first. Single-song mode ignores a video's playlist context. Other clipboard contents are ignored.

Right-click a soundboard row to rename or delete its file on this PC. Rename preserves the audio extension and rejects conflicts. Delete asks for confirmation. For the bot owner (Discord ID 218880619659132928), a personal token from !token grants shared management: shared renames/deletions also commit the change to GitHub. Other users edit their local copies only. GitHub credentials remain on the bot. Sync removes unchanged retired copies before uploading, preventing old filenames from being resurrected. Locally modified copies are preserved. Owner edits require a working connection and the bot repository token. **Change folder**, to the right of the local folder path, selects and remembers your private clip folder.

Renaming or deleting an active clip stops only that file's playback on your relay and waits for FFmpeg to release its file handle. Other clips and music continue. Windows file-lock errors are retried briefly; if another application still holds the file, the error is shown.

### Clip volume and labels
Each soundboard row has a Volume slider: centered 0% preserves original loudness, -100% mutes, and +100% doubles amplitude (peaks are limited to avoid overflow). Changes apply to currently playing and future clips; YouTube volume is unaffected.

Use the Label dropdown to choose a saved label or No label. Right-click a clip and choose Add Label to create and assign one. Change Label Color opens seven pastel color choices and updates the label cell for every clip with that label. Volume, labels, and colors are saved locally, including for shared clips; rename preserves settings. Only clicking the filename plays a clip.

Shared displays ☑ for shared clips and ☐ for local clips. Name, Shared, and Label headers sort; click the current primary header again to reverse it. Clicking a different header makes it primary while retaining earlier columns as tie-breakers, with filenames as the final fallback. Shared starts with checked entries first. For example, Label then Shared sorts by checked status, label name, then filename. Embedded controls follow header resizing.

### Tabbed desktop layout
YouTube Music and Soundboard have separate tabs with full-width lists. There is no Discord destination picker: your personal token resolves the voice channel you are in for playback and controls, including when you move channels. Join voice, then click a clip or paste a YouTube link; the bot joins automatically for playback.

Drag a soundboard column header onto another header to move it to that position. Column order is saved locally; sorting is separate from dragging, and only the Name column plays clips regardless of position. Volume and label controls track their columns after reorder or resizing.

Right-click a local clip and choose **Share** to copy it into sharedclips and sync while keeping the local copy. **Share then del local** copies first, removes the local copy after FFmpeg releases it, then syncs. Conflicting filenames are never overwritten; both files are preserved on copy errors. Volume and label settings carry to the shared copy. Sync failures leave the shared copy on your PC for retry.

The selected tab is taller than the inactive tab. Soundboard actions sit beside the tabs in the top toolbar; column headers are navy, and label dropdowns have no raised indicator.

Sharing first checks GitHub for retired filenames and conflicting content, requiring a working relay connection. If either check fails, the local file is kept.

### Automatic soundboard loudness
Local and shared clips are measured once and adjusted toward -20 dBFS average loudness, with peaks kept at or below -7 dBFS before the slider applies. Quiet clips gain up to 24 dB; louder clips are reduced. The manual per-clip slider applies afterward and remains independent. Original audio files are unchanged. Measurements are cached in relay-config.json and refreshed when a file changes; the first play can take slightly longer while analyzing. YouTube audio is unaffected.

Soundboard rows are 18 pixels tall (half the previous height). Refresh files, Stop all clips, and Sync clips are at the bottom left. Automatic updates, Check for updates, Install update now, and Change folder sit at the top right beside the tabs.

Soundboard columns keep their widths when the window expands or the last column shrinks, leaving unused space on the right. Opening a label dropdown highlights that clip row; the selection stays after applying a label.

Clip audio normalization is prepared in the background when the library loads at startup or refresh. Unchanged files reuse saved measurements. Soundboard sliders, label menus, and colors are preloaded for all clips and retained while scrolling.

Soundboard volume sliders and colored labels use two canvas surfaces instead of a native Windows control for every row. Their preloaded graphics scroll together with the table, avoiding per-control repaint trails.

YouTube Music has saved favorites/playlists on the left and the shared Discord queue on the right. Right-click a queue song to Favorite it or Remove from queue. Double-click a saved item to enqueue it; saved playlist links add up to 50 songs. Favorites are saved locally and retained across relay updates. Right-click a saved item to remove it from favorites. Removing the currently playing song advances music without stopping soundboard clips.

Use Search clips / labels above the soundboard for instant case-insensitive filtering. Clear restores the complete list. Filtering never deletes files or stops audio. Canvas rows align below the actual Windows column header height, including after opening the soundboard from the music tab.

## Play soundboard through your own Discord microphone

Check **Play soundboard directly instead of through bot** beside **Update automatically**. The choice and device selections are remembered. This mode affects soundboard clips only; YouTube music still uses the bot.

First-time setup:
1. Install [VB-CABLE](https://vb-audio.com/Cable/) and restart Windows if its installer requests it.
2. Open **Direct audio settings**. Choose **CABLE Input** as the virtual microphone output, your speakers/headphones for monitoring, and your physical microphone for optional voice passthrough. The Windows WASAPI entries are preferred. Devices must support stereo output and 48 kHz; the microphone uses mono 48 kHz.
3. In Discord **User Settings → Voice & Video**, choose **CABLE Output** as Input Device and your normal speakers/headphones as Output Device. If Discord suppresses clips, adjust its noise suppression and input sensitivity.
4. Click clip filenames. Audio goes to the cable and speakers, without requesting bot playback or connecting the bot to voice. Your microphone is mixed into the cable only, so you do not hear your own microphone through the speakers. Existing normalization and clip sliders still apply.

**Stop all clips** stops direct clips but keeps microphone passthrough active. Unchecking the box stops direct playback and microphone passthrough and restores bot playback for clips. When unchecking, switch Discord's Input Device back to your physical microphone. The relay cannot change Discord's device selection automatically. Direct mode requires the relay to remain open; closing it releases its audio devices. Auto-updates wait for direct clips to finish, while manual installation can stop them immediately.

## Text to speech

Open the **TTS** tab, enter text, and press **Enter** or **Play text**. Shift+Enter adds a new line. Join a Discord voice channel first; the bot follows the Discord user associated with your relay token. Speech is mixed with music and clips, with no now-playing post. **Stop speech / clips** stops current bot soundboard audio.

Piper generates speech locally. Choose Lessac, Amy, Ryan, or Sam (US English), or Alan or Alba (British English) in the Voice selector; your choice is remembered. Each model downloads once on first use. The first use downloads the voice into `Files/TTS/voice`; later generation works offline (the Discord connection still needs internet). Messages are limited to 1,000 characters. Recent speech is cached under `Files/TTS/audio` and is not uploaded to sharedclips. This tab always plays through the bot, independently of the Direct soundboard switch.

Piper is GPL-3.0-or-later: https://github.com/OHF-Voice/piper1-gpl . Its source and license are available there and in the pinned PyPI source distribution at https://pypi.org/project/piper-tts/1.8.0/ . Voice attribution/license details are downloaded beside the model as `MODEL_CARD`: https://huggingface.co/rhasspy/piper-voices/blob/main/en/en_US/lessac/medium/MODEL_CARD .

## Translation and voice catalog

TTS includes every Piper model/quality option for English, Dutch, German, French, and Bulgarian. Choose a **Voice** and, for multi-speaker models, a **Speaker**. Choices are saved. Full catalog: https://github.com/dasaright/TacoBot/blob/main/VOICES.md .

Select **Translate from** and a target language, then press **Translate**. The result appears below without replacing your input. **Play translated** automatically selects a voice in the output language when needed. Translation uses local CTranslate2 inference with Argos language models; models download on first use and are cached in `Files/Translation`. Non-English pairs translate through English. No translation API fees, and text is processed on your PC. Model downloads can take time and use disk space; translation quality varies.

Argos models/source: https://github.com/argosopentech/argos-translate and https://github.com/argosopentech/argospm-index . CTranslate2: https://github.com/OpenNMT/CTranslate2 .

## Managing TTS voices

**Manage voices** lists every available model, whether it is downloaded, whether it is enabled in your TTS selector, and its speaker count. Search/filter the list, select one or more models, and choose **Download & enable**, **Enable**, **Disable**, or **Remove download**. **Use selected** chooses the selected model. Disabling keeps its download on disk; removing a download also disables it. Enabling a model without downloading lets it download on first playback. Your enabled list is saved across restarts.

In the TTS input, **Ctrl+Backspace** deletes the previous word (or selection), and **Shift+Backspace** clears the input.

## Qt desktop presentation

The packaged application now starts from `relay_agent/desktop.py`. Qt owns one
shared backing store for the desktop, and Music, Soundboard, and TTS are resident
pages in a `StackAll` layout. Switching pages raises the prepared page without
constructing widgets, scanning clips, resetting models, or running a transition.
Soundboard rows use a cached data model and one native paint delegate rather than
individual slider/label child windows. The asyncio audio/network engine still
runs separately from the desktop event loop.

Existing `relay-config.json`, tokens, clip labels, per-clip volume, folders,
favorites/playlists, direct audio devices, independent tab volumes, sorting,
and auto-update preferences carry forward. No new setup is required.

Windows release tests capture actual visible frames with 1,000 clips and 500
queue songs. They reject mixed/blank frames and require the requested pixels to
reach the screen with a 95th-percentile latency below 50 ms. Diagnostics include
reference screenshots and `timing.json`; these tests measure presentation rather
than only checking the selected tab index.

### Moving from the older Tk desktop to Qt

The older updater has a 250 MB limit for extracted program files. The Qt package exceeds that limit, so older installations need one manual upgrade: close TacoBot, download the latest TacoBot-Windows.zip, and extract its program files into the existing relay folder, replacing them. Keep relay-config.json, its backup, and your clip folders. The new updater retains the 250 MB download cap and allows up to 500 MB after extraction, with the same checksum and fixed file allowlist checks. Future updates use the normal updater.
