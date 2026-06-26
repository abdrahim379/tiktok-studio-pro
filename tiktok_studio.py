# -*- coding: utf-8 -*-
import streamlit as st
import subprocess, os, datetime, tempfile, json, re, time, random, uuid
import zipfile, io, traceback, requests
from io import BytesIO

# ── Resolve tool paths (works even when Streamlit strips PATH) ──────────
def _find_bin(name):
    """Return full path of a binary, checking common locations."""
    candidates = [
        f"/opt/homebrew/bin/{name}",      # macOS Apple Silicon
        f"/usr/local/bin/{name}",          # macOS Intel / Linux
        f"/usr/bin/{name}",
        name,                              # fallback: rely on PATH
    ]
    # ffmpeg/ffprobe use -version (single dash), yt-dlp uses --version
    version_flag = "-version" if name in ("ffmpeg", "ffprobe") else "--version"
    for p in candidates:
        try:
            r = subprocess.run([p, version_flag], capture_output=True, timeout=5)
            if r.returncode == 0:
                return p
        except Exception:
            continue
    return name  # last resort

YTDLP   = _find_bin("yt-dlp")
FFMPEG  = _find_bin("ffmpeg")
FFPROBE = _find_bin("ffprobe")

st.set_page_config(
    page_title="TikTok Studio",
    page_icon="🎬",
    layout="wide"
)

st.markdown("""
<style>
    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
    }
    .stTabs [data-baseweb="tab"] {
        height: 50px;
        padding: 0 24px;
        background-color: #1a1a1a;
        border-radius: 10px 10px 0 0;
        color: white;
        font-weight: 600;
        font-size: 16px;
    }
    .stTabs [aria-selected="true"] {
        background-color: #ee0a78 !important;
    }
</style>
""", unsafe_allow_html=True)

st.title("🎬 TikTok Studio")
st.markdown("**Four tools in one app** — Download videos, generate variants, refresh metadata, or find similar content")
st.markdown("---")

tab1, tab2, tab3, tab4 = st.tabs(["📥 TikTok Downloader", "🎛️ Variant Generator", "🔄 Refresh Metadata", "🔍 Find Similar"])


# ============================================================
# TAB 1 — TikTok Downloader (converted from Flask)
# ============================================================
with tab1:
    st.header("📥 TikTok Video Extractor & Downloader")
    st.markdown("Extract videos from any TikTok profile/hashtag URL, select the ones you want, and download them as a ZIP.")

    # ---- helpers ----
    def format_number(num):
        if not num:
            return "N/A"
        try:
            num = int(num)
        except (ValueError, TypeError):
            return str(num)
        if num >= 1_000_000:
            return f"{num/1_000_000:.1f}M"
        elif num >= 1_000:
            return f"{num/1_000:.1f}K"
        return str(num)

    def check_ytdlp():
        try:
            r = subprocess.run([YTDLP, "--version"], capture_output=True, text=True, timeout=5)
            return r.returncode == 0
        except Exception:
            return False

    def extract_with_ytdlp(url):
        cmd = [
            YTDLP,
            "--dump-json",
            "--flat-playlist",
            "--no-warnings",
            "--no-check-certificate",
            "--extractor-retries", "3",
            "--playlist-end", "9999",   # no artificial limit
            url
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        videos = []
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            try:
                info = json.loads(line)
                video_url = info.get("webpage_url") or info.get("url") or ""
                vid_id    = info.get("id", "")
                if not vid_id:
                    continue
                videos.append({
                    "id":        vid_id,
                    "title":     info.get("title", "TikTok Video"),
                    "thumbnail": info.get("thumbnail", ""),
                    "views":     format_number(info.get("view_count", 0)),
                    "likes":     format_number(info.get("like_count", 0)),
                    "url":       video_url,
                })
            except json.JSONDecodeError:
                continue
        return videos

    def download_videos_ytdlp(video_list, output_dir, progress_bar, status_text):
        """Download using the actual video URL stored at extraction time.
        Retries with fallback formats, handles per-video timeouts, and
        reports exactly which videos failed and why."""
        downloaded = []
        failed = []
        total = len(video_list)

        # Build a map id → url from session state for lookup
        id_to_url = {v["id"]: v["url"] for v in st.session_state.dl_videos}

        # Format fallbacks — try best combined first, fall back progressively
        FORMAT_ATTEMPTS = [
            "bestvideo+bestaudio/best",
            "best",
            "worst",  # last resort — better to get something than nothing
        ]

        for idx, vid in enumerate(video_list):
            vid_id  = vid if isinstance(vid, str) else vid["id"]
            vid_url = id_to_url.get(vid_id, f"https://www.tiktok.com/@x/video/{vid_id}")

            progress_bar.progress(idx / total)

            output_template = os.path.join(output_dir, f"{vid_id}.%(ext)s")
            success = False
            last_err = ""

            for attempt, fmt in enumerate(FORMAT_ATTEMPTS):
                status_text.markdown(
                    f"⬇️ Downloading **{idx+1}/{total}** — `{vid_id}`"
                    + (f" (retry {attempt+1}/{len(FORMAT_ATTEMPTS)})" if attempt else "")
                )
                cmd = [
                    YTDLP,
                    "-f", fmt,
                    "--merge-output-format", "mp4",
                    "--no-warnings",
                    "--no-check-certificate",
                    "--extractor-retries", "5",
                    "--retries", "10",
                    "--fragment-retries", "10",
                    "--retry-sleep", "3",
                    "--sleep-requests", "1",
                    "-o", output_template,
                    vid_url,
                ]
                try:
                    result = subprocess.run(cmd, capture_output=True, timeout=240)
                except subprocess.TimeoutExpired:
                    last_err = "timed out after 240s"
                    time.sleep(2)
                    continue

                if result.returncode == 0:
                    # Check the file actually exists and is non-empty
                    for fname in os.listdir(output_dir):
                        if fname.startswith(vid_id):
                            fpath = os.path.join(output_dir, fname)
                            if os.path.getsize(fpath) > 0:
                                downloaded.append(fpath)
                                success = True
                            break
                    if success:
                        break
                else:
                    last_err = result.stderr.decode(errors="replace")[-300:] if result.stderr else "unknown error"

                # small delay before retrying with a different format
                time.sleep(1.5)

            if not success:
                failed.append((vid_id, last_err))
                status_text.markdown(f"⚠️ Failed `{vid_id}` after {len(FORMAT_ATTEMPTS)} attempts — {last_err}")

            progress_bar.progress((idx + 1) / total)

            # Be polite to TikTok between videos to avoid rate-limit blocks
            time.sleep(1)

        if failed:
            status_text.markdown(
                f"⚠️ **{len(failed)}/{total}** video(s) failed: "
                + ", ".join(f"`{vid_id}`" for vid_id, _ in failed)
            )

        return downloaded, failed

    # ---- UI state ----
    if "dl_videos" not in st.session_state:
        st.session_state.dl_videos = []
    if "dl_selected" not in st.session_state:
        st.session_state.dl_selected = set()

    # ---- Step 1: Extract ----
    st.subheader("Step 1 — Extract Videos")
    col_url, col_btn = st.columns([4, 1])
    with col_url:
        tiktok_url = st.text_input(
            "TikTok URL",
            placeholder="https://www.tiktok.com/@username or hashtag URL",
            label_visibility="collapsed"
        )
    with col_btn:
        extract_btn = st.button("🔍 Extract", use_container_width=True)

    if extract_btn:
        if not tiktok_url:
            st.error("Please enter a TikTok URL.")
        elif "tiktok.com" not in tiktok_url:
            st.error("Please enter a valid TikTok URL.")
        elif not check_ytdlp():
            st.error("yt-dlp is not installed. Run: `pip install yt-dlp`")
        else:
            with st.spinner("Extracting videos..."):
                try:
                    videos = extract_with_ytdlp(tiktok_url)
                    if videos:
                        st.session_state.dl_videos = videos
                        st.session_state.dl_selected = set()
                        st.success(f"✅ Found **{len(videos)}** videos!")
                    else:
                        st.warning("No videos found. Check the URL and make sure the profile is public.")
                except Exception as e:
                    st.error(f"Extraction failed: {e}")

    # ---- Step 2: Select ----
    if st.session_state.dl_videos:
        st.markdown("---")
        st.subheader("Step 2 — Select Videos")

        col_sel, col_desel = st.columns(2)
        with col_sel:
            if st.button("✅ Select All", use_container_width=True):
                st.session_state.dl_selected = {v["id"] for v in st.session_state.dl_videos}
        with col_desel:
            if st.button("❌ Deselect All", use_container_width=True):
                st.session_state.dl_selected = set()

        # Grid of video cards
        cols_per_row = 4
        videos = st.session_state.dl_videos
        for row_start in range(0, len(videos), cols_per_row):
            row_videos = videos[row_start: row_start + cols_per_row]
            cols = st.columns(cols_per_row)
            for col, video in zip(cols, row_videos):
                with col:
                    is_selected = video["id"] in st.session_state.dl_selected
                    border_color = "#ee0a78" if is_selected else "#ddd"
                    check_icon = "✅" if is_selected else "⬜"

                    st.markdown(
                        f"""<div style="border:3px solid {border_color};border-radius:12px;
                        padding:10px;margin-bottom:8px;text-align:center;">
                        <div style="font-size:22px;">{check_icon}</div>
                        <div style="font-size:12px;font-weight:600;margin:6px 0;
                        overflow:hidden;max-height:2.8em;">{video['title'][:60]}</div>
                        <div style="font-size:11px;color:#666;">
                        👁️ {video['views']} &nbsp; ❤️ {video['likes']}</div></div>""",
                        unsafe_allow_html=True,
                    )
                    if st.button(
                        "Select" if not is_selected else "Deselect",
                        key=f"sel_{video['id']}",
                        use_container_width=True,
                    ):
                        if is_selected:
                            st.session_state.dl_selected.discard(video["id"])
                        else:
                            st.session_state.dl_selected.add(video["id"])
                        st.rerun()

        # ---- Step 3: Download ----
        st.markdown("---")
        st.subheader("Step 3 — Download")
        selected_count = len(st.session_state.dl_selected)
        st.info(f"**{selected_count}** video(s) selected")

        if st.button(
            f"📥 Download {selected_count} Video(s) as ZIP",
            disabled=selected_count == 0,
            use_container_width=True,
        ):
            # Pass full video objects so downloader has the real URL
            selected_videos = [
                v for v in st.session_state.dl_videos
                if v["id"] in st.session_state.dl_selected
            ]
            progress_bar = st.progress(0)
            status_text = st.empty()

            with tempfile.TemporaryDirectory() as tmpdir:
                try:
                    downloaded, failed = download_videos_ytdlp(selected_videos, tmpdir, progress_bar, status_text)

                    if downloaded:
                        zip_buf = BytesIO()
                        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
                            for fp in downloaded:
                                zf.write(fp, os.path.basename(fp))
                        zip_buf.seek(0)

                        progress_bar.progress(1.0)
                        if failed:
                            status_text.warning(
                                f"⚠️ {len(downloaded)}/{selected_count} video(s) downloaded. "
                                f"{len(failed)} failed: " + ", ".join(f"`{vid_id}`" for vid_id, _ in failed)
                            )
                        else:
                            status_text.success(f"✅ All {len(downloaded)} video(s) ready!")

                        st.download_button(
                            label=f"⬇️ Download ZIP ({len(downloaded)} videos)",
                            data=zip_buf,
                            file_name=f"tiktok_videos_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.zip",
                            mime="application/zip",
                        )

                        if failed:
                            with st.expander(f"⚠️ {len(failed)} video(s) failed — details"):
                                for vid_id, err in failed:
                                    st.markdown(f"**{vid_id}**: {err}")
                    else:
                        status_text.error("No videos downloaded. Check yt-dlp and the video IDs.")
                        if failed:
                            with st.expander("Error details"):
                                for vid_id, err in failed:
                                    st.markdown(f"**{vid_id}**: {err}")
                except Exception as e:
                    status_text.error(f"Download failed: {e}")


# ============================================================
# TAB 2 — Variant Generator (original Streamlit app)
# ============================================================
with tab2:
    st.header("🎛️ TikTok Variant Generator")
    st.markdown("Generate up to **5 variants** of your videos (120fps • 4000kbps • 1080×1920)")

    # ---- Constants ----
    TARGET_W, TARGET_H = 1080, 1920
    MAX_FPS = 120
    SAFE_THREADS = ["-threads", "2"]
    TIME_RE = re.compile(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)")

    # ---- Helpers ----
    def vg_run(cmd):
        try:
            return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  text=True, timeout=30)
        except Exception as e:
            st.error(f"run() error: {e}")
            return None

    def ffprobe_json(path, select="format"):
        try:
            p = vg_run([FFPROBE, "-v", "error", "-show_entries", select, "-of", "json", path])
            return json.loads(p.stdout) if p else {}
        except Exception as e:
            st.error(f"ffprobe_json error: {e}")
            return {}

    def ffprobe_streams(path):
        try:
            p = vg_run([FFPROBE, "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=width,height,r_frame_rate,avg_frame_rate",
                        "-of", "json", path])
            return json.loads(p.stdout).get("streams", [{}])[0] if p else {}
        except Exception as e:
            st.error(f"ffprobe_streams error: {e}")
            return {}

    def parse_fps(rate_str):
        try:
            if not rate_str or rate_str == "0/0":
                return None
            n, d = rate_str.split("/")
            return float(n) / float(d) if float(d) else float(n)
        except Exception:
            return None

    def get_video_meta(path):
        try:
            s = ffprobe_streams(path)
            return s.get("width"), s.get("height"), parse_fps(
                s.get("avg_frame_rate") or s.get("r_frame_rate")
            )
        except Exception as e:
            st.error(f"get_video_meta error: {e}")
            return None, None, None

    def get_duration_seconds(path):
        try:
            data = ffprobe_json(path, select="format")
            dur = data.get("format", {}).get("duration")
            return float(dur) if dur else None
        except Exception:
            return None

    def parse_progress(line):
        try:
            m = TIME_RE.search(line or "")
            if not m:
                return None
            h, m_, s = m.groups()
            return int(h) * 3600 + int(m_) * 60 + float(s)
        except Exception:
            return None

    def run_ffmpeg_with_progress(cmd, total_seconds, progress_cb=None, log_cb=None):
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                    text=True, bufsize=1, errors="replace")
            start_time = time.time()
            last_progress_time = start_time
            timeout_seconds = 600

            for line in proc.stderr:
                if "time=" in line and log_cb:
                    log_cb(line.rstrip("\n"))
                t = parse_progress(line)
                if t is not None and total_seconds and progress_cb:
                    pct = max(0, min(100, int(t / total_seconds * 100)))
                    progress_cb(pct)
                    last_progress_time = time.time()

                if time.time() - last_progress_time > 120:
                    st.error("FFmpeg stuck (no progress 120s). Terminating.")
                    proc.terminate()
                    time.sleep(2)
                    if proc.poll() is None:
                        proc.kill()
                    return -2

                if time.time() - start_time > timeout_seconds:
                    st.error(f"FFmpeg timeout after {timeout_seconds}s. Terminating.")
                    proc.terminate()
                    time.sleep(2)
                    if proc.poll() is None:
                        proc.kill()
                    return -3

            rc = proc.wait()
            if progress_cb:
                progress_cb(100 if rc == 0 else 0)
            return rc
        except Exception as e:
            st.error(f"run_ffmpeg_with_progress error: {e}\n{traceback.format_exc()}")
            return -1

    def ffmpeg_prefix(use_hw):
        return [FFMPEG, "-y", "-hwaccel", "videotoolbox"] if use_hw else [FFMPEG, "-y"]

    def build_ffmpeg_cmd(
        input_path, out_path, *,
        variant_name, use_hw_decode, use_ultra_stable,
        hook_type, hook_dur, hook_img_path, hook_vid_path, hook_keep_audio,
        zoom_mode, audio_mode, add_blank_intro, blank_intro_sec, overlay_img_path=None,
    ):
        try:
            fps = 120
            br_k = 4000
            w, h, in_fps = get_video_meta(input_path)
            out_fps = min(fps, MAX_FPS)

            base_zoom = []
            if zoom_mode == "zoom + crop":
                base_zoom.append("scale=iw*1.01:ih*1.01,crop=iw:ih")
            elif zoom_mode == "zoom inverse + pad":
                base_zoom.append("scale=iw*0.99:ih*0.99,pad=iw:ih:(ow-iw)/2:(oh-ih)/2")

            if (w, h) == (TARGET_W, TARGET_H) and zoom_mode == "aucun":
                target_norm = f"format=yuv420p,fps={out_fps}"
            else:
                target_norm = (
                    f"scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=decrease,"
                    f"pad={TARGET_W}:{TARGET_H}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p,fps={out_fps}"
                )
            base_vf = ",".join([*base_zoom, target_norm]) if base_zoom else target_norm

            metadata_clear = [
                "-map_metadata", "-1",
                "-metadata", f"title=Variant_{variant_name}",
                "-metadata", f"creation_time={datetime.datetime.now().isoformat()}",
            ]

            if use_ultra_stable:
                v_common = (["-b:v", f"{br_k}k", "-c:v", "libx264", "-preset", "veryfast",
                             "-pix_fmt", "yuv420p"] + SAFE_THREADS + metadata_clear)
            else:
                v_common = (["-b:v", f"{br_k}k", "-c:v", "h264_videotoolbox",
                             "-pix_fmt", "yuv420p"] + SAFE_THREADS + metadata_clear)

            def a_simple():
                if audio_mode == "pitch +1%":
                    return ["-filter:a", "asetrate=44100*1.01,aresample=44100", "-c:a", "aac"]
                elif audio_mode == "pitch -1%":
                    return ["-filter:a", "asetrate=44100*0.99,aresample=44100", "-c:a", "aac"]
                return ["-c:a", "aac", "-ar", "44100", "-ac", "2"]

            def a_complex(label_in):
                if audio_mode == "pitch +1%":
                    return f"{label_in}asetrate=44100*1.01,aresample=44100,aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo"
                if audio_mode == "pitch -1%":
                    return f"{label_in}asetrate=44100*0.99,aresample=44100,aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo"
                return f"{label_in}aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo"

            a_out = ["-c:a", "aac", "-ar", "44100", "-ac", "2"]

            # Variant 5 overlay
            if variant_name == "5" and overlay_img_path:
                cmd = ffmpeg_prefix(use_hw_decode) + ["-i", input_path, "-i", overlay_img_path]
                fc = (f"[0:v]{base_vf}[base];"
                      f"[1:v]scale={TARGET_W}:{TARGET_H}[overlay];"
                      f"[base][overlay]overlay=0:0[vout];"
                      f"{a_complex('[0:a]')}[aout]")
                cmd += ["-filter_complex", fc, "-map", "[vout]", "-map", "[aout]"] + v_common + a_out + [out_path]
                return cmd, br_k, get_duration_seconds(input_path) or 0

            # No hook
            if hook_type == "None":
                if add_blank_intro and blank_intro_sec > 0:
                    cmd = ffmpeg_prefix(use_hw_decode) + [
                        "-f", "lavfi", "-t", f"{blank_intro_sec}",
                        "-i", f"color=size={TARGET_W}x{TARGET_H}:color=black:rate={out_fps},format=yuv420p",
                        "-f", "lavfi", "-t", f"{blank_intro_sec}",
                        "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                        "-i", input_path,
                        "-filter_complex",
                        f"[2:v]{base_vf}[vm];"
                        f"{a_complex('[2:a]')}[am];"
                        "[0:v][1:a][vm][am]concat=n=2:v=1:a=1[vout][aout]",
                        "-map", "[vout]", "-map", "[aout]",
                    ] + v_common + a_out + [out_path]
                    return cmd, br_k, (blank_intro_sec or 0) + (get_duration_seconds(input_path) or 0)
                else:
                    cmd = (ffmpeg_prefix(use_hw_decode)
                           + ["-i", input_path, "-vf", base_vf, "-r", str(out_fps),
                              "-map", "0:v:0", "-map", "0:a?"]
                           + v_common + a_simple() + [out_path])
                    return cmd, br_k, get_duration_seconds(input_path) or 0

            # Image overlay
            if hook_type == "Image overlay":
                cmd = ffmpeg_prefix(use_hw_decode) + ["-i", input_path, "-loop", "1", "-t", f"{hook_dur}", "-i", hook_img_path]
                fc = (f"[0:v]{base_vf}[base];"
                      f"[1:v][base]scale2ref=w=iw:h=ih[img][b2];"
                      f"[b2][img]overlay=(W-w)/2:(H-h)/2:enable='between(t,0,{hook_dur})'[vout];"
                      f"{a_complex('[0:a]')}[aout]")
                cmd += ["-filter_complex", fc, "-map", "[vout]", "-map", "[aout]"] + v_common + a_out + [out_path]
                return cmd, br_k, get_duration_seconds(input_path) or 0

            # Video prepend
            if hook_type == "Video prepend":
                cmd = ffmpeg_prefix(use_hw_decode) + ["-i", hook_vid_path, "-i", input_path]
                tnorm = (f"scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=decrease,"
                         f"pad={TARGET_W}:{TARGET_H}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p,fps={out_fps}")
                fc = f"[0:v]{tnorm}[vh];[1:v]{base_vf}[vm];"
                if hook_keep_audio:
                    fc += f"{a_complex('[0:a]')}[ah];"
                else:
                    fc += "anullsrc=channel_layout=stereo:sample_rate=44100[ah];"
                fc += f"{a_complex('[1:a]')}[am];[vh][ah][vm][am]concat=n=2:v=1:a=1[vout][aout]"
                cmd += ["-filter_complex", fc, "-map", "[vout]", "-map", "[aout]"] + v_common + a_out + [out_path]
                return cmd, br_k, (get_duration_seconds(hook_vid_path) or 0) + (get_duration_seconds(input_path) or 0)

            # Video overlay
            if hook_type == "Video overlay":
                cmd = ffmpeg_prefix(use_hw_decode) + ["-i", input_path, "-i", hook_vid_path]
                tnorm = (f"scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=decrease,"
                         f"pad={TARGET_W}:{TARGET_H}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p,fps={out_fps}")
                fc = (f"[0:v]{base_vf}[base];[1:v]{tnorm}[hv];"
                      f"[base][hv]overlay=(W-w)/2:(H-h)/2:enable='between(t,0,{hook_dur})'[vout];"
                      f"{a_complex('[0:a]')}[aout]")
                cmd += ["-filter_complex", fc, "-map", "[vout]", "-map", "[aout]"] + v_common + a_out + [out_path]
                return cmd, br_k, get_duration_seconds(input_path) or 0

            # Fallback
            cmd = (ffmpeg_prefix(use_hw_decode)
                   + ["-i", input_path, "-vf", base_vf, "-r", str(out_fps),
                      "-map", "0:v:0", "-map", "0:a?"]
                   + v_common + a_out + [out_path])
            return cmd, br_k, get_duration_seconds(input_path) or 0

        except Exception as e:
            st.error(f"build_ffmpeg_cmd error: {e}\n{traceback.format_exc()}")
            return None, None, None

    # ---- Sidebar-style options inside tab ----
    with st.expander("⚙️ Options", expanded=True):
        col_a, col_b = st.columns(2)
        with col_a:
            use_ultra_stable = st.checkbox("Mode ultra-stable (libx264/veryfast)", value=True)
            use_hw_decode = st.checkbox("Hardware decode (Videotoolbox)", value=False)
            zoom_mode = st.radio("Creative zoom mode", ["zoom + crop", "zoom inverse + pad", "aucun"], index=0)
        with col_b:
            selected_variants = st.multiselect(
                "Variants to generate",
                options=["1", "2", "3", "4", "5"],
                default=["1", "2", "3", "4"],
                help="1=Base | 2=+Intro+Hook | 3=+Pitch+1% | 4=+Pitch-1% | 5=Overlay",
            )
            hook_type = st.selectbox("Hook type (V2/V3/V4)", ["None", "Image overlay", "Video prepend", "Video overlay"])

    with st.expander("🎣 Hook & Overlay files"):
        hook_img = None
        hook_vid = None
        hook_keep_audio = False
        overlay_img = None

        if hook_type == "Image overlay":
            hook_img = st.file_uploader("Hook image (PNG/JPG)", type=["png", "jpg", "jpeg"], key="vg_hook_img")
        elif hook_type in ("Video prepend", "Video overlay"):
            hook_vid = st.file_uploader("Hook video (mp4/mov)", type=["mp4", "mov", "m4v"], key="vg_hook_vid")
            if hook_type == "Video prepend":
                hook_keep_audio = st.checkbox("Keep hook audio (prepend)", value=True)

        overlay_img = st.file_uploader("Variant 5 — Overlay/Border PNG (transparent)", type=["png"], key="vg_overlay")

    st.markdown("### 📥 Videos to process")
    videos = st.file_uploader(
        "Drop one or more videos",
        type=["mp4", "mov", "m4v"],
        accept_multiple_files=True,
        key="vg_videos",
    )

    st.markdown("---")
    run_btn = st.button("🚀 Generate Selected Variants", type="primary")

    if run_btn and videos:
        if not selected_variants:
            st.error("Select at least one variant.")
        else:
            try:
                all_variants_def = {
                    "1": {"name": "1", "hook_dur": 0.3, "add_intro": False, "intro_sec": 0.0, "audio": "normal"},
                    "2": {"name": "2", "hook_dur": 0.1, "add_intro": True,  "intro_sec": 0.01, "audio": "normal"},
                    "3": {"name": "3", "hook_dur": 0.1, "add_intro": True,  "intro_sec": 0.01, "audio": "pitch +1%"},
                    "4": {"name": "4", "hook_dur": 0.1, "add_intro": True,  "intro_sec": 0.01, "audio": "pitch -1%"},
                    "5": {"name": "5", "hook_dur": 0.3, "add_intro": False, "intro_sec": 0.0, "audio": "normal"},
                }
                variants = [all_variants_def[v] for v in selected_variants]
                total_tasks = len(videos) * len(variants)
                completed_tasks = 0
                overall_progress = st.progress(0)
                overall_status = st.empty()
                overall_status.markdown(f"### {len(videos)} video(s) × {len(variants)} variant(s) = **{total_tasks} tasks**")

                all_generated_files = []

                # Save hook/overlay files once
                hook_img_path = hook_vid_path = overlay_img_path = None
                if hook_type == "Image overlay" and hook_img:
                    with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(hook_img.name)[1]) as f:
                        f.write(hook_img.read())
                        hook_img_path = f.name
                if hook_type in ("Video prepend", "Video overlay") and hook_vid:
                    with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(hook_vid.name)[1]) as f:
                        f.write(hook_vid.read())
                        hook_vid_path = f.name
                if overlay_img:
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as f:
                        f.write(overlay_img.read())
                        overlay_img_path = f.name

                for video_idx, file in enumerate(videos):
                    st.markdown(f"---\n## 🎬 Video {video_idx+1}/{len(videos)}: **{file.name}**")
                    input_path = None
                    try:
                        with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(file.name)[1]) as tmp:
                            tmp.write(file.read())
                            input_path = tmp.name
                    except Exception as e:
                        st.error(f"Failed to save input: {e}")
                        continue

                    base_name = os.path.splitext(os.path.basename(file.name))[0]

                    for variant_idx, variant in enumerate(variants):
                        try:
                            out_name = f"{base_name}_{variant['name']}.mp4"
                            out_tmp = os.path.join(tempfile.gettempdir(), out_name)
                            overall_status.markdown(
                                f"Video **{video_idx+1}/{len(videos)}** • "
                                f"Variant **{variant['name']}** • "
                                f"Progress: **{completed_tasks}/{total_tasks}**"
                            )
                            st.markdown(f"### ▶️ Variant {variant['name']}")
                            cmd, br, est_total = build_ffmpeg_cmd(
                                input_path, out_tmp,
                                variant_name=variant["name"],
                                use_hw_decode=use_hw_decode,
                                use_ultra_stable=use_ultra_stable,
                                hook_type=hook_type,
                                hook_dur=variant["hook_dur"],
                                hook_img_path=hook_img_path,
                                hook_vid_path=hook_vid_path,
                                hook_keep_audio=hook_keep_audio,
                                zoom_mode=zoom_mode,
                                audio_mode=variant["audio"],
                                add_blank_intro=variant["add_intro"],
                                blank_intro_sec=variant["intro_sec"],
                                overlay_img_path=overlay_img_path,
                            )
                            if cmd is None:
                                st.error(f"Failed to build command for variant {variant['name']}")
                                completed_tasks += 1
                                overall_progress.progress(completed_tasks / total_tasks)
                                continue

                            pbar = st.progress(0)
                            ptxt = st.empty()
                            log_exp = st.expander(f"FFmpeg logs — variant {variant['name']}", expanded=False)
                            log_box = log_exp.empty()

                            def prog_cb(p, _pb=pbar, _pt=ptxt):
                                _pb.progress(p)
                                _pt.markdown(f"Progress: **{p}%**")

                            def log_cb(line, _lb=log_box):
                                _lb.code(line, language="bash")

                            rc = run_ffmpeg_with_progress(cmd, est_total or 0, progress_cb=prog_cb, log_cb=log_cb)

                            if rc == 0:
                                st.success(f"✅ Variant {variant['name']} done → {out_name}")
                                all_generated_files.append({"path": out_tmp, "name": out_name})
                            else:
                                st.error(f"FFmpeg error code {rc} for variant {variant['name']}")

                            completed_tasks += 1
                            overall_progress.progress(completed_tasks / total_tasks)

                        except Exception as e:
                            st.error(f"Error on variant {variant['name']}: {e}\n{traceback.format_exc()}")
                            completed_tasks += 1
                            overall_progress.progress(completed_tasks / total_tasks)

                    if input_path:
                        try:
                            os.unlink(input_path)
                        except Exception:
                            pass

                # ---- Final ZIP download ----
                st.markdown("---\n# 📦 Done")
                if all_generated_files:
                    zip_buffer = BytesIO()
                    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                        for fi in all_generated_files:
                            try:
                                zf.write(fi["path"], fi["name"])
                            except Exception as e:
                                st.warning(f"Could not add {fi['name']}: {e}")
                    zip_buffer.seek(0)
                    zip_filename = f"TikTok_Variants_{datetime.datetime.now().strftime('%Y%m%d_%H%M')}.zip"
                    st.success(f"🎉 {len(all_generated_files)} variant(s) generated!")
                    st.balloons()
                    st.download_button(
                        label=f"⬇️ Download all variants ({len(all_generated_files)} files)",
                        data=zip_buffer,
                        file_name=zip_filename,
                        mime="application/zip",
                        key="vg_download_zip",
                    )
                    for fi in all_generated_files:
                        try:
                            os.unlink(fi["path"])
                        except Exception:
                            pass
                    for p in [hook_img_path, hook_vid_path, overlay_img_path]:
                        if p:
                            try:
                                os.unlink(p)
                            except Exception:
                                pass
                else:
                    st.error("No videos were successfully generated.")

            except Exception as e:
                st.error(f"FATAL: {e}\n{traceback.format_exc()}")

    elif not videos:
        st.info("Upload videos above, then click **Generate Selected Variants**.")
        st.markdown("""
**Variant guide:**
| # | Description |
|---|---|
| 1 | Base — 120fps, 4000kbps |
| 2 | + Invisible black intro (0.01s) + Hook (0.1s) |
| 3 | Variant 2 + Audio pitch +1% |
| 4 | Variant 2 + Audio pitch −1% |
| 5 | + Custom overlay/border PNG |
        """)


# ============================================================
# TAB 3 — Refresh Metadata (make re-uploads look "new")
# ============================================================
with tab3:
    st.header("🔄 Refresh Metadata")
    st.markdown(
        "Upload videos you already downloaded from TikTok and re-export them with "
        "**brand-new metadata + a unique digital fingerprint** — so they look fresh "
        "to the algorithm when you re-upload them."
    )

    # ---- Device / location pools ----------------------------------
    RM_DEVICE_PROFILES = [
        {"make": "Apple",   "model": "iPhone 15 Pro Max", "software": "17.4.1"},
        {"make": "Apple",   "model": "iPhone 15 Pro",     "software": "17.4"},
        {"make": "Apple",   "model": "iPhone 14 Pro Max", "software": "17.3.1"},
        {"make": "Apple",   "model": "iPhone 14",         "software": "17.2"},
        {"make": "Apple",   "model": "iPhone 13 Pro",     "software": "16.6.1"},
        {"make": "Apple",   "model": "iPhone 13",         "software": "16.5"},
        {"make": "samsung", "model": "SM-S928B",          "software": "Android 14"},
        {"make": "samsung", "model": "SM-S918B",          "software": "Android 14"},
        {"make": "samsung", "model": "SM-A546E",          "software": "Android 13"},
    ]

    # Default batch device = iPhone (matches "same iPhone, same country" request).
    # The re-roll button can still pick any profile, including Samsung.
    RM_IPHONE_PROFILES = [p for p in RM_DEVICE_PROFILES if p["make"] == "Apple"]

    # All locations across Saudi Arabia — picked per video so each one looks
    # like it was filmed in a different city, but always inside KSA.
    RM_SAUDI_LOCATIONS = [
        ("Riyadh",          24.7136, 46.6753),
        ("Jeddah",          21.4858, 39.1925),
        ("Mecca",           21.3891, 39.8579),
        ("Medina",          24.5247, 39.5692),
        ("Dammam",          26.4207, 50.0888),
        ("Khobar",          26.2172, 50.1971),
        ("Dhahran",         26.2361, 50.0393),
        ("Taif",            21.2703, 40.4158),
        ("Abha",            18.2164, 42.5053),
        ("Khamis Mushait",  18.3000, 42.7333),
        ("Tabuk",           28.3998, 36.5715),
        ("Hail",            27.5114, 41.7208),
        ("Buraidah",        26.3260, 43.9750),
        ("Unaizah",         26.0844, 43.9935),
        ("Najran",          17.4933, 44.1277),
        ("Jazan",           16.8892, 42.5611),
        ("Yanbu",           24.0895, 38.0618),
        ("Al Ahsa",         25.3833, 49.5856),
        ("Jubail",          27.0046, 49.6225),
        ("Qatif",           26.5196, 50.0078),
        ("Hafr Al-Batin",   28.4342, 45.9601),
        ("Al Kharj",        24.1556, 47.3350),
        ("Sakaka",          29.9697, 40.2064),
        ("Arar",            30.9753, 41.0381),
        ("Al Bahah",        20.0129, 41.4677),
    ]

    # Saudi Arabia Standard Time = UTC+3, no daylight saving
    RM_KSA_TZ = datetime.timedelta(hours=3)

    RM_INTENSITY = {
        "Light":  {"crop": (0.985, 0.995), "bright": (-0.012, 0.012), "contrast": (0.99, 1.01),
                   "sat": (0.98, 1.02),   "noise": (1, 2), "pitch": (0.997, 1.003)},
        "Medium": {"crop": (0.965, 0.985), "bright": (-0.02, 0.02),  "contrast": (0.97, 1.03),
                   "sat": (0.95, 1.05),   "noise": (2, 4), "pitch": (0.99, 1.01)},
        "Strong": {"crop": (0.94, 0.965),  "bright": (-0.035, 0.035), "contrast": (0.94, 1.06),
                   "sat": (0.90, 1.10),   "noise": (3, 6), "pitch": (0.98, 1.02)},
    }

    # ---- Helpers ----------------------------------------------------
    def rm_random_riyadh_datetime(days_ago_max=10):
        """Return (local_riyadh_dt, utc_dt) at a believable 'human' hour."""
        now_riyadh = datetime.datetime.utcnow() + RM_KSA_TZ
        days_ago = random.randint(0, days_ago_max)
        base_date = (now_riyadh - datetime.timedelta(days=days_ago)).date()
        # Realistic posting hours: late morning to midnight
        hour   = random.randint(8, 23)
        minute = random.randint(0, 59)
        second = random.randint(0, 59)
        local_dt = datetime.datetime.combine(
            base_date, datetime.time(hour, minute, second)
        )
        utc_dt = local_dt - RM_KSA_TZ
        return local_dt, utc_dt

    def rm_random_metadata_args(device_profile, city_name, lat, lon, days_ago_max=10):
        lat += random.uniform(-0.04, 0.04)
        lon += random.uniform(-0.04, 0.04)

        local_dt, utc_dt = rm_random_riyadh_datetime(days_ago_max)
        creation_time = utc_dt.strftime("%Y-%m-%dT%H:%M:%S.000000Z")
        qt_creation   = local_dt.strftime("%Y-%m-%dT%H:%M:%S+0300")
        iso6709 = f"{lat:+.4f}{lon:+.4f}+000.000/"
        unique_id = uuid.uuid4().hex

        args = [
            "-map_metadata", "-1",
            "-map_chapters", "-1",
            "-metadata", f"creation_time={creation_time}",
            "-metadata", f"com.apple.quicktime.make={device_profile['make']}",
            "-metadata", f"com.apple.quicktime.model={device_profile['model']}",
            "-metadata", f"com.apple.quicktime.software={device_profile['software']}",
            "-metadata", f"com.apple.quicktime.creationdate={qt_creation}",
            "-metadata", f"com.apple.quicktime.location.ISO6709={iso6709}",
            "-metadata", "com.apple.quicktime.location.accuracy.horizontal=5.000000",
            "-metadata", f"com.apple.quicktime.content.identifier={unique_id}",
            "-metadata", f"encoder=HW_{unique_id[:10]}",
            "-metadata", "title=",
            "-metadata", "comment=",
            "-metadata", "description=",
        ]
        info = {
            "device": f"{device_profile['make']} {device_profile['model']}",
            "software": device_profile["software"],
            "location": f"{city_name}, Saudi Arabia",
            "date": local_dt.strftime("%Y-%m-%d %H:%M") + " (KSA time)",
            "id": unique_id[:12],
        }
        return args, info

    def rm_build_visual_filter(intensity):
        r = RM_INTENSITY[intensity]
        crop = random.uniform(*r["crop"])
        vf = (
            f"crop=iw*{crop:.4f}:ih*{crop:.4f},"
            f"scale=trunc(iw/{crop:.4f}/2)*2:trunc(ih/{crop:.4f}/2)*2"
        )
        bright   = random.uniform(*r["bright"])
        contrast = random.uniform(*r["contrast"])
        sat      = random.uniform(*r["sat"])
        vf += f",eq=brightness={bright:.4f}:contrast={contrast:.4f}:saturation={sat:.4f}"
        noise = random.randint(*r["noise"])
        vf += f",noise=alls={noise}:allf=t+u"
        return vf

    def rm_build_audio_filter(intensity):
        r = RM_INTENSITY[intensity]
        pitch = random.uniform(*r["pitch"])
        return f"asetrate=44100*{pitch:.5f},aresample=44100,atempo={1/pitch:.5f}"

    def rm_random_filename(ext="mp4"):
        ts  = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        rid = uuid.uuid4().hex[:8].upper()
        return f"VID_{ts}_{rid}.{ext}"

    def rm_build_cmd(input_path, output_path, intensity, do_visual, do_audio, target_bitrate_k,
                     device_profile, city_name, lat, lon, days_ago_max=10):
        cmd = [FFMPEG, "-y", "-i", input_path]

        if do_visual:
            cmd += ["-vf", rm_build_visual_filter(intensity)]
        if do_audio:
            cmd += ["-af", rm_build_audio_filter(intensity)]

        meta_args, meta_info = rm_random_metadata_args(device_profile, city_name, lat, lon, days_ago_max)
        cmd += meta_args

        cmd += [
            "-c:v", "libx264", "-preset", "veryfast",
            "-b:v", f"{target_bitrate_k}k",
            "-maxrate", f"{int(target_bitrate_k*1.15)}k",
            "-bufsize", f"{int(target_bitrate_k*2)}k",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            output_path,
        ]
        return cmd, meta_info

    # ---- UI -----------------------------------------------------------
    st.subheader("Step 1 — Upload Videos")
    rm_files = st.file_uploader(
        "Upload one or more videos (already downloaded from TikTok)",
        type=["mp4", "mov", "avi", "mkv", "webm"],
        accept_multiple_files=True,
        key="rm_files",
    )

    # ---- One consistent device for the whole batch -----------------
    if "rm_device" not in st.session_state:
        st.session_state.rm_device = random.choice(RM_IPHONE_PROFILES)

    rm_dev_col1, rm_dev_col2 = st.columns([4, 1])
    with rm_dev_col1:
        _dev = st.session_state.rm_device
        st.markdown(
            f"📱 **Device for this entire batch:** {_dev['make']} {_dev['model']} "
            f"(`{_dev['software']}`) — every refreshed video will look like it came "
            f"from this same phone."
        )
    with rm_dev_col2:
        if st.button("🔁 Re-roll device", use_container_width=True):
            st.session_state.rm_device = random.choice(RM_DEVICE_PROFILES)
            st.rerun()

    st.subheader("Step 2 — Refresh Options")
    rm_col1, rm_col2, rm_col3 = st.columns(3)
    with rm_col1:
        rm_intensity = st.select_slider(
            "Visual change intensity",
            options=["Light", "Medium", "Strong"],
            value="Medium",
            help="Higher = bigger crop/zoom, color shift & noise → stronger fingerprint change, "
                 "but slightly more visible to viewers.",
        )
    with rm_col2:
        rm_do_visual = st.checkbox("Apply visual micro-changes (crop/zoom/color/noise)", value=True)
        rm_do_audio  = st.checkbox("Apply audio pitch micro-shift", value=True)
    with rm_col3:
        rm_bitrate = st.slider("Output bitrate (kbps)", 2000, 6000, 4000, step=250)

    st.info(
        "ℹ️ Each video gets: **stripped original metadata** → **same device** "
        "(picked above) → **a different Saudi Arabia city/GPS location & a "
        "realistic Riyadh-time (UTC+3) creation date** → **unique content ID** → "
        "**new random filename**. With visual/audio changes enabled, the video's "
        "digital fingerprint also changes. All videos look like they came from the "
        "same iPhone/phone, just filmed in different cities across Saudi Arabia."
    )

    st.subheader("Step 3 — Refresh")
    rm_go = st.button("🔄 Refresh Metadata for All Videos", use_container_width=True,
                       disabled=not rm_files)

    if rm_go and rm_files:
        rm_results = []
        overall = st.progress(0)
        overall_txt = st.empty()
        total = len(rm_files)

        # Shuffle Saudi cities so consecutive videos don't repeat locations;
        # if there are more videos than cities, cycle through again.
        rm_city_pool = RM_SAUDI_LOCATIONS.copy()
        random.shuffle(rm_city_pool)
        rm_device = st.session_state.rm_device

        for idx, rm_file in enumerate(rm_files):
            overall_txt.markdown(f"Processing **{idx+1}/{total}** — `{rm_file.name}`")

            in_path = os.path.join(tempfile.gettempdir(), f"rm_in_{uuid.uuid4().hex[:8]}_{rm_file.name}")
            with open(in_path, "wb") as f:
                f.write(rm_file.getbuffer())

            out_name = rm_random_filename(ext="mp4")
            out_path = os.path.join(tempfile.gettempdir(), out_name)

            try:
                city_name, city_lat, city_lon = rm_city_pool[idx % len(rm_city_pool)]
                # Spread videos across the last ~10 days so timestamps look natural
                days_ago_max = max(1, min(10, total))
                cmd, meta_info = rm_build_cmd(
                    in_path, out_path, rm_intensity, rm_do_visual, rm_do_audio, rm_bitrate,
                    rm_device, city_name, city_lat, city_lon, days_ago_max
                )

                dur = get_duration_seconds(in_path) or 0

                pbar = st.progress(0)
                ptxt = st.empty()
                log_exp = st.expander(f"FFmpeg logs — {rm_file.name}", expanded=False)
                log_box = log_exp.empty()

                def prog_cb(p, _pb=pbar, _pt=ptxt):
                    _pb.progress(p)
                    _pt.markdown(f"Progress: **{p}%**")

                def log_cb(line, _lb=log_box):
                    _lb.code(line, language="bash")

                rc = run_ffmpeg_with_progress(cmd, dur, progress_cb=prog_cb, log_cb=log_cb)

                if rc == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                    st.success(f"✅ `{rm_file.name}` → `{out_name}`")
                    st.markdown(
                        f"&nbsp;&nbsp;📱 **{meta_info['device']}** ({meta_info['software']}) "
                        f"• 📍 {meta_info['location']} • 🗓️ {meta_info['date']} "
                        f"• 🆔 `{meta_info['id']}`"
                    )
                    rm_results.append({"path": out_path, "name": out_name})
                else:
                    st.error(f"❌ FFmpeg error (code {rc}) for `{rm_file.name}`")

            except Exception as e:
                st.error(f"Error processing `{rm_file.name}`: {e}\n{traceback.format_exc()}")
            finally:
                try:
                    os.unlink(in_path)
                except Exception:
                    pass

            overall.progress((idx + 1) / total)

        # ---- ZIP download ----
        st.markdown("---")
        if rm_results:
            zip_buf = BytesIO()
            with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for r in rm_results:
                    try:
                        zf.write(r["path"], r["name"])
                    except Exception as e:
                        st.warning(f"Could not add {r['name']}: {e}")
            zip_buf.seek(0)

            st.success(f"🎉 {len(rm_results)}/{total} video(s) refreshed and ready!")
            st.balloons()
            st.download_button(
                label=f"⬇️ Download Refreshed Videos ({len(rm_results)} files)",
                data=zip_buf,
                file_name=f"Refreshed_Videos_{datetime.datetime.now().strftime('%Y%m%d_%H%M')}.zip",
                mime="application/zip",
                key="rm_download_zip",
            )

            for r in rm_results:
                try:
                    os.unlink(r["path"])
                except Exception:
                    pass
        else:
            st.error("No videos were successfully refreshed.")

    elif not rm_files:
        st.info("Upload one or more videos above, then click **Refresh Metadata for All Videos**.")
        st.markdown("""
**What gets changed:**
| Item | Change |
|---|---|
| File name | Randomized (`VID_<timestamp>_<id>.mp4`) |
| Device make/model | **One iPhone for the whole batch** (re-rollable above) |
| GPS location | A different city across **Saudi Arabia** for each video |
| Creation date/time | Realistic Riyadh time (UTC+3), human posting hours, spread over recent days |
| Content ID | New random unique identifier |
| Visual (optional) | Subtle crop/zoom + color + noise |
| Audio (optional) | Subtle pitch micro-shift |

All original metadata (old device info, old GPS, old timestamps) is **stripped** before the new values are written. Using one consistent device with only the location/time changing per clip mimics how a real person posts multiple videos from the same phone while traveling around the country.
        """)


# ============================================================
# TAB 4 — Find Similar (visual search on TikTok only)
# ============================================================
with tab4:
    st.header("🔍 Find Similar Videos on TikTok")
    st.markdown(
        "Upload an image (screenshot, frame, photo) and find **visually similar "
        "TikTok videos**. Uses Google Lens filtered to TikTok content only."
    )

    # ---- API key setup -----------------------------------------------
    st.subheader("Step 1 — SerpAPI Key")
    st.markdown(
        "This feature uses [SerpAPI](https://serpapi.com/) to run a Google Lens "
        "visual search. **Free tier = 100 searches/month** — no credit card needed."
    )

    if "serpapi_key" not in st.session_state:
        st.session_state.serpapi_key = ""

    fs_key = st.text_input(
        "SerpAPI Key",
        value=st.session_state.serpapi_key,
        type="password",
        help="Get your free key at https://serpapi.com/manage-api-key",
        key="fs_serpapi_input",
    )
    if fs_key:
        st.session_state.serpapi_key = fs_key

    # ---- Image upload ------------------------------------------------
    st.subheader("Step 2 — Upload Image")
    fs_image = st.file_uploader(
        "Upload the image you want to find similar TikTok videos for",
        type=["png", "jpg", "jpeg", "webp", "bmp"],
        key="fs_image",
    )

    if fs_image:
        st.image(fs_image, caption="Your image", width=300)

    # ---- Search ------------------------------------------------------
    st.subheader("Step 3 — Search")
    fs_go = st.button(
        "🔍 Find Similar on TikTok",
        use_container_width=True,
        disabled=not (fs_image and st.session_state.serpapi_key),
    )

    def fs_upload_temp_image(image_bytes, filename):
        """Upload image to a free temporary host and return a public URL."""
        try:
            resp = requests.post(
                "https://tmpfiles.org/api/v1/upload",
                files={"file": (filename, image_bytes)},
                timeout=30,
            )
            if resp.status_code == 200:
                data = resp.json()
                url = data.get("data", {}).get("url", "")
                if url:
                    # tmpfiles.org returns viewer URL; convert to direct URL
                    return url.replace("tmpfiles.org/", "tmpfiles.org/dl/")
        except Exception:
            pass

        # Fallback: file.io
        try:
            resp = requests.post(
                "https://file.io",
                files={"file": (filename, image_bytes)},
                timeout=30,
            )
            if resp.status_code == 200:
                return resp.json().get("link", "")
        except Exception:
            pass
        return ""

    def fs_google_lens_search(image_url, api_key):
        """Run Google Lens search via SerpAPI, return TikTok-only results."""
        params = {
            "engine": "google_lens",
            "url": image_url,
            "api_key": api_key,
        }
        resp = requests.get("https://serpapi.com/search.json", params=params, timeout=60)
        resp.raise_for_status()
        data = resp.json()

        tiktok_results = []

        # Check visual_matches
        for match in data.get("visual_matches", []):
            link = match.get("link", "")
            if "tiktok.com" in link:
                tiktok_results.append({
                    "title": match.get("title", "TikTok Video"),
                    "link": link,
                    "thumbnail": match.get("thumbnail", ""),
                    "source": match.get("source", "TikTok"),
                    "snippet": match.get("snippet", ""),
                })

        # Check knowledge_graph
        for item in data.get("knowledge_graph", []):
            for link_info in item.get("images", []):
                link = link_info.get("link", "")
                if "tiktok.com" in link:
                    tiktok_results.append({
                        "title": item.get("title", "TikTok Video"),
                        "link": link,
                        "thumbnail": link_info.get("thumbnail", ""),
                        "source": "TikTok",
                        "snippet": "",
                    })

        # Check reverse_image_search results via text_results
        for item in data.get("text_results", []):
            link = item.get("link", "")
            if "tiktok.com" in link:
                tiktok_results.append({
                    "title": item.get("title", "TikTok Video"),
                    "link": link,
                    "thumbnail": item.get("thumbnail", ""),
                    "source": "TikTok",
                    "snippet": item.get("snippet", ""),
                })

        # Deduplicate by link
        seen = set()
        unique = []
        for r in tiktok_results:
            if r["link"] not in seen:
                seen.add(r["link"])
                unique.append(r)
        return unique, data

    if fs_go and fs_image and st.session_state.serpapi_key:
        with st.spinner("Uploading image for visual search..."):
            image_bytes = fs_image.getvalue()
            image_url = fs_upload_temp_image(image_bytes, fs_image.name)

        if not image_url:
            st.error("Failed to upload image to temporary host. Please try again.")
        else:
            with st.spinner("Searching Google Lens for similar TikTok content..."):
                try:
                    results, raw_data = fs_google_lens_search(
                        image_url, st.session_state.serpapi_key
                    )
                except requests.exceptions.HTTPError as e:
                    st.error(f"SerpAPI error: {e}")
                    results = []
                    raw_data = {}
                except Exception as e:
                    st.error(f"Search failed: {e}")
                    results = []
                    raw_data = {}

            if results:
                st.success(f"Found **{len(results)}** similar TikTok video(s)!")
                st.markdown("---")

                for i, r in enumerate(results):
                    col_thumb, col_info = st.columns([1, 3])
                    with col_thumb:
                        if r["thumbnail"]:
                            try:
                                st.image(r["thumbnail"], width=150)
                            except Exception:
                                st.markdown("🎬")
                        else:
                            st.markdown("🎬")
                    with col_info:
                        st.markdown(f"**{r['title']}**")
                        if r["snippet"]:
                            st.caption(r["snippet"])
                        st.markdown(f"[🔗 Open on TikTok]({r['link']})")

                        # Extract video ID for download
                        vid_match = re.search(r"/video/(\d+)", r["link"])
                        if vid_match:
                            vid_id = vid_match.group(1)
                            dl_key = f"fs_dl_{i}_{vid_id}"
                            if st.button(f"⬇️ Download", key=dl_key):
                                with st.spinner(f"Downloading {vid_id}..."):
                                    dl_dir = tempfile.mkdtemp()
                                    dl_out = os.path.join(dl_dir, f"{vid_id}.%(ext)s")
                                    dl_cmd = [
                                        YTDLP, "-f", "bestvideo+bestaudio/best",
                                        "--merge-output-format", "mp4",
                                        "--no-warnings", "--no-check-certificate",
                                        "--retries", "5",
                                        "-o", dl_out, r["link"],
                                    ]
                                    dl_result = subprocess.run(
                                        dl_cmd, capture_output=True, timeout=240
                                    )
                                    if dl_result.returncode == 0:
                                        for fname in os.listdir(dl_dir):
                                            if fname.startswith(vid_id):
                                                fpath = os.path.join(dl_dir, fname)
                                                with open(fpath, "rb") as vf:
                                                    st.download_button(
                                                        "💾 Save Video",
                                                        data=vf.read(),
                                                        file_name=fname,
                                                        mime="video/mp4",
                                                        key=f"fs_save_{i}_{vid_id}",
                                                    )
                                                break
                                    else:
                                        st.warning("Download failed — video may be private or region-locked.")
                    st.markdown("---")

                # Download all button
                if len(results) > 1:
                    all_vid_links = [
                        r["link"] for r in results
                        if re.search(r"/video/(\d+)", r["link"])
                    ]
                    if all_vid_links and st.button(
                        f"⬇️ Download All {len(all_vid_links)} Videos",
                        use_container_width=True,
                        key="fs_dl_all",
                    ):
                        dl_dir = tempfile.mkdtemp()
                        dl_progress = st.progress(0)
                        dl_status = st.empty()
                        downloaded_files = []
                        for j, link in enumerate(all_vid_links):
                            vid_match = re.search(r"/video/(\d+)", link)
                            vid_id = vid_match.group(1) if vid_match else f"video_{j}"
                            dl_status.markdown(f"Downloading **{j+1}/{len(all_vid_links)}**...")
                            dl_out = os.path.join(dl_dir, f"{vid_id}.%(ext)s")
                            dl_cmd = [
                                YTDLP, "-f", "bestvideo+bestaudio/best",
                                "--merge-output-format", "mp4",
                                "--no-warnings", "--no-check-certificate",
                                "--retries", "5", "--sleep-requests", "1",
                                "-o", dl_out, link,
                            ]
                            try:
                                dl_result = subprocess.run(
                                    dl_cmd, capture_output=True, timeout=240
                                )
                                if dl_result.returncode == 0:
                                    for fname in os.listdir(dl_dir):
                                        if fname.startswith(vid_id):
                                            downloaded_files.append(
                                                os.path.join(dl_dir, fname)
                                            )
                                            break
                            except Exception:
                                pass
                            dl_progress.progress((j + 1) / len(all_vid_links))
                            time.sleep(1)

                        if downloaded_files:
                            zip_buf = BytesIO()
                            with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
                                for fp in downloaded_files:
                                    zf.write(fp, os.path.basename(fp))
                            zip_buf.seek(0)
                            st.download_button(
                                f"💾 Download ZIP ({len(downloaded_files)} videos)",
                                data=zip_buf,
                                file_name=f"Similar_TikTok_{datetime.datetime.now().strftime('%Y%m%d_%H%M')}.zip",
                                mime="application/zip",
                                key="fs_dl_all_zip",
                            )
                        else:
                            st.error("No videos could be downloaded.")

            else:
                st.warning("No similar TikTok videos found for this image.")
                # Show what was found in general (non-TikTok) for context
                all_matches = raw_data.get("visual_matches", [])
                if all_matches:
                    with st.expander(f"Google Lens found {len(all_matches)} results on other sites"):
                        for m in all_matches[:10]:
                            st.markdown(f"- [{m.get('title', 'Result')}]({m.get('link', '#')}) — {m.get('source', '')}")
                st.info(
                    "💡 **Tip:** Try a clearer or more unique image. Screenshots of the "
                    "actual TikTok video frame work best."
                )

    elif not fs_image:
        st.info("Upload an image above to find similar TikTok videos.")
        st.markdown("""
**How it works:**
1. You provide your **SerpAPI key** (free at [serpapi.com](https://serpapi.com/))
2. Upload a screenshot or image frame
3. Google Lens does a **visual search** and we filter results to **TikTok only**
4. You can preview and download any matching videos directly

**Best results with:**
- Clear screenshots of actual TikTok video frames
- Product images, faces, or distinctive scenes
- Images with unique visual elements (not generic landscapes)
        """)
