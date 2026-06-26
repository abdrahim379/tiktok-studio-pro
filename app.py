# -*- coding: utf-8 -*-
import streamlit as st
import subprocess, os, datetime, tempfile, json, re, time, random, uuid
import zipfile, io, traceback, requests, hashlib
from io import BytesIO

# ── Resolve tool paths ────────────────────────────────────────────────
def _find_bin(name):
    candidates = [
        f"/opt/homebrew/bin/{name}",
        f"/usr/local/bin/{name}",
        f"/usr/bin/{name}",
        name,
    ]
    version_flag = "-version" if name in ("ffmpeg", "ffprobe") else "--version"
    for p in candidates:
        try:
            r = subprocess.run([p, version_flag], capture_output=True, timeout=5)
            if r.returncode == 0:
                return p
        except Exception:
            continue
    return name

YTDLP   = _find_bin("yt-dlp")
FFMPEG  = _find_bin("ffmpeg")
FFPROBE = _find_bin("ffprobe")

# ══════════════════════════════════════════════════════════════════════
# AUTH SYSTEM
# ══════════════════════════════════════════════════════════════════════
USERS_DB_FILE = "users_db.json"

FEATURE_LABELS = {
    "downloader": "📥 TikTok Downloader",
    "variants": "🎛️ Variant Generator",
    "metadata": "🔄 Refresh Metadata",
    "find_similar": "🔍 Find Similar",
}

def hash_pw(password):
    return hashlib.sha256(password.encode()).hexdigest()

def load_db():
    if os.path.exists(USERS_DB_FILE):
        try:
            with open(USERS_DB_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    db = {
        "settings": {"serpapi_key": ""},
        "users": {
            "admin": {
                "name": "Admin",
                "email": "admin",
                "password_hash": hash_pw("admin123"),
                "role": "admin",
                "permissions": list(FEATURE_LABELS.keys()),
                "created": datetime.datetime.now().strftime("%Y-%m-%d"),
            }
        }
    }
    save_db(db)
    return db

def save_db(db):
    with open(USERS_DB_FILE, "w") as f:
        json.dump(db, f, indent=2)

def verify_login(email, password):
    db = load_db()
    pw_hash = hash_pw(password)
    for uid, u in db["users"].items():
        if u["email"] == email and u["password_hash"] == pw_hash:
            return uid, u
    return None, None

# ══════════════════════════════════════════════════════════════════════
# PAGE CONFIG
# ══════════════════════════════════════════════════════════════════════
st.set_page_config(page_title="TikTok Studio Pro", page_icon="🎬", layout="wide")

st.markdown("""
<style>
    .stTabs [data-baseweb="tab-list"] { gap: 8px; }
    .stTabs [data-baseweb="tab"] {
        height: 50px; padding: 0 24px;
        background-color: #1a1a1a; border-radius: 10px 10px 0 0;
        color: white; font-weight: 600; font-size: 16px;
    }
    .stTabs [aria-selected="true"] { background-color: #ee0a78 !important; }
    .login-box {
        max-width: 400px; margin: 80px auto; padding: 40px;
        border-radius: 16px; border: 1px solid #333;
    }
</style>
""", unsafe_allow_html=True)

# ── Session state init ─────────────────────────────────────────────
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
    st.session_state.user_id = None
    st.session_state.user_data = None

# ══════════════════════════════════════════════════════════════════════
# LOGIN SCREEN
# ══════════════════════════════════════════════════════════════════════
if not st.session_state.authenticated:
    st.markdown("<div class='login-box'>", unsafe_allow_html=True)
    st.markdown("## 🎬 TikTok Studio Pro")
    st.markdown("Sign in to continue")

    with st.form("login_form"):
        login_email = st.text_input("Email / Username")
        login_pw = st.text_input("Password", type="password")
        login_btn = st.form_submit_button("Sign In", use_container_width=True)

    if login_btn:
        uid, user = verify_login(login_email, login_pw)
        if user:
            st.session_state.authenticated = True
            st.session_state.user_id = uid
            st.session_state.user_data = user
            st.rerun()
        else:
            st.error("Invalid email or password.")

    st.markdown("</div>", unsafe_allow_html=True)
    st.stop()

# ══════════════════════════════════════════════════════════════════════
# AUTHENTICATED — SIDEBAR
# ══════════════════════════════════════════════════════════════════════
user = st.session_state.user_data
is_admin = user["role"] == "admin"

with st.sidebar:
    st.markdown(f"### 👤 {user['name']}")
    st.caption(f"{user['email']} • {'Admin' if is_admin else 'User'}")
    st.markdown("---")

    perms = user["permissions"]
    st.markdown("**Your access:**")
    for p in perms:
        st.markdown(f"- {FEATURE_LABELS.get(p, p)}")

    st.markdown("---")

    # Change password
    with st.expander("🔑 Change Password"):
        with st.form("change_pw"):
            old_pw = st.text_input("Current password", type="password", key="cpw_old")
            new_pw = st.text_input("New password", type="password", key="cpw_new")
            new_pw2 = st.text_input("Confirm new password", type="password", key="cpw_new2")
            cpw_btn = st.form_submit_button("Update Password")
        if cpw_btn:
            if hash_pw(old_pw) != user["password_hash"]:
                st.error("Current password is wrong.")
            elif len(new_pw) < 4:
                st.error("New password must be at least 4 characters.")
            elif new_pw != new_pw2:
                st.error("Passwords don't match.")
            else:
                db = load_db()
                db["users"][st.session_state.user_id]["password_hash"] = hash_pw(new_pw)
                save_db(db)
                st.session_state.user_data["password_hash"] = hash_pw(new_pw)
                st.success("Password updated!")

    if st.button("🚪 Sign Out", use_container_width=True):
        st.session_state.authenticated = False
        st.session_state.user_id = None
        st.session_state.user_data = None
        st.rerun()

# ══════════════════════════════════════════════════════════════════════
# BUILD TABS BASED ON PERMISSIONS
# ══════════════════════════════════════════════════════════════════════
st.title("🎬 TikTok Studio Pro")
st.markdown("---")

tab_names = []
tab_keys = []
for key, label in FEATURE_LABELS.items():
    if key in perms:
        tab_names.append(label)
        tab_keys.append(key)

if is_admin:
    tab_names.append("⚙️ Admin Dashboard")
    tab_keys.append("admin")

if not tab_names:
    st.warning("You don't have access to any features. Contact the admin.")
    st.stop()

tabs = st.tabs(tab_names)


# ══════════════════════════════════════════════════════════════════════
# SHARED HELPERS (used by multiple tabs)
# ══════════════════════════════════════════════════════════════════════
TARGET_W, TARGET_H = 1080, 1920
MAX_FPS = 120
SAFE_THREADS = ["-threads", "2"]
TIME_RE = re.compile(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)")

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
    except Exception:
        return {}

def ffprobe_streams(path):
    try:
        p = vg_run([FFPROBE, "-v", "error", "-select_streams", "v:0",
                    "-show_entries", "stream=width,height,r_frame_rate,avg_frame_rate",
                    "-of", "json", path])
        return json.loads(p.stdout).get("streams", [{}])[0] if p else {}
    except Exception:
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
    except Exception:
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
                proc.terminate()
                time.sleep(2)
                if proc.poll() is None:
                    proc.kill()
                return -2
            if time.time() - start_time > timeout_seconds:
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
        st.error(f"FFmpeg error: {e}")
        return -1

def ffmpeg_prefix(use_hw):
    return [FFMPEG, "-y", "-hwaccel", "videotoolbox"] if use_hw else [FFMPEG, "-y"]


# ══════════════════════════════════════════════════════════════════════
# TAB CONTENT FUNCTIONS
# ══════════════════════════════════════════════════════════════════════

# ──────────────────────────────────────────────────────────────────────
# TAB: DOWNLOADER
# ──────────────────────────────────────────────────────────────────────
def render_downloader():
    st.header("📥 TikTok Video Extractor & Downloader")
    st.markdown("Extract videos from any TikTok profile/hashtag URL, select the ones you want, and download them as a ZIP.")

    def check_ytdlp():
        try:
            r = subprocess.run([YTDLP, "--version"], capture_output=True, text=True, timeout=5)
            return r.returncode == 0
        except Exception:
            return False

    def extract_with_ytdlp(url):
        cmd = [YTDLP, "--dump-json", "--flat-playlist", "--no-warnings",
               "--no-check-certificate", "--extractor-retries", "3",
               "--playlist-end", "9999", url]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        videos = []
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            try:
                info = json.loads(line)
                video_url = info.get("webpage_url") or info.get("url") or ""
                vid_id = info.get("id", "")
                if not vid_id:
                    continue
                videos.append({
                    "id": vid_id,
                    "title": info.get("title", "TikTok Video"),
                    "thumbnail": info.get("thumbnail", ""),
                    "views": format_number(info.get("view_count", 0)),
                    "likes": format_number(info.get("like_count", 0)),
                    "url": video_url,
                })
            except json.JSONDecodeError:
                continue
        return videos

    def download_videos_ytdlp(video_list, output_dir, progress_bar, status_text):
        downloaded = []
        failed = []
        total = len(video_list)
        id_to_url = {v["id"]: v["url"] for v in st.session_state.dl_videos}
        FORMAT_ATTEMPTS = ["bestvideo+bestaudio/best", "best", "worst"]
        for idx, vid in enumerate(video_list):
            vid_id = vid if isinstance(vid, str) else vid["id"]
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
                cmd = [YTDLP, "-f", fmt, "--merge-output-format", "mp4",
                       "--no-warnings", "--no-check-certificate",
                       "--extractor-retries", "5", "--retries", "10",
                       "--fragment-retries", "10", "--retry-sleep", "3",
                       "--sleep-requests", "1", "-o", output_template, vid_url]
                try:
                    result = subprocess.run(cmd, capture_output=True, timeout=240)
                except subprocess.TimeoutExpired:
                    last_err = "timed out after 240s"
                    time.sleep(2)
                    continue
                if result.returncode == 0:
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
                time.sleep(1.5)
            if not success:
                failed.append((vid_id, last_err))
            progress_bar.progress((idx + 1) / total)
            time.sleep(1)
        return downloaded, failed

    if "dl_videos" not in st.session_state:
        st.session_state.dl_videos = []
    if "dl_selected" not in st.session_state:
        st.session_state.dl_selected = set()

    st.subheader("Step 1 — Extract Videos")
    col_url, col_btn = st.columns([4, 1])
    with col_url:
        tiktok_url = st.text_input("TikTok URL", placeholder="https://www.tiktok.com/@username or hashtag URL", label_visibility="collapsed")
    with col_btn:
        extract_btn = st.button("🔍 Extract", use_container_width=True)

    if extract_btn:
        if not tiktok_url:
            st.error("Please enter a TikTok URL.")
        elif "tiktok.com" not in tiktok_url:
            st.error("Please enter a valid TikTok URL.")
        elif not check_ytdlp():
            st.error("yt-dlp is not installed.")
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
                    if st.button("Select" if not is_selected else "Deselect",
                                 key=f"sel_{video['id']}", use_container_width=True):
                        if is_selected:
                            st.session_state.dl_selected.discard(video["id"])
                        else:
                            st.session_state.dl_selected.add(video["id"])
                        st.rerun()

        st.markdown("---")
        st.subheader("Step 3 — Download")
        selected_count = len(st.session_state.dl_selected)
        st.info(f"**{selected_count}** video(s) selected")
        if st.button(f"📥 Download {selected_count} Video(s) as ZIP",
                     disabled=selected_count == 0, use_container_width=True):
            selected_videos = [v for v in st.session_state.dl_videos if v["id"] in st.session_state.dl_selected]
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
                            status_text.warning(f"⚠️ {len(downloaded)}/{selected_count} downloaded. {len(failed)} failed.")
                        else:
                            status_text.success(f"✅ All {len(downloaded)} video(s) ready!")
                        st.download_button(label=f"⬇️ Download ZIP ({len(downloaded)} videos)",
                                           data=zip_buf,
                                           file_name=f"tiktok_videos_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.zip",
                                           mime="application/zip")
                        if failed:
                            with st.expander(f"⚠️ {len(failed)} failed — details"):
                                for vid_id, err in failed:
                                    st.markdown(f"**{vid_id}**: {err}")
                    else:
                        status_text.error("No videos downloaded.")
                except Exception as e:
                    status_text.error(f"Download failed: {e}")


# ──────────────────────────────────────────────────────────────────────
# TAB: VARIANT GENERATOR
# ──────────────────────────────────────────────────────────────────────
def render_variants():
    st.header("🎛️ TikTok Variant Generator")
    st.markdown("Generate up to **5 variants** of your videos (120fps • 4000kbps • 1080×1920)")

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
            metadata_clear = ["-map_metadata", "-1",
                              "-metadata", f"title=Variant_{variant_name}",
                              "-metadata", f"creation_time={datetime.datetime.now().isoformat()}"]
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

            if variant_name == "5" and overlay_img_path:
                cmd = ffmpeg_prefix(use_hw_decode) + ["-i", input_path, "-i", overlay_img_path]
                fc = (f"[0:v]{base_vf}[base];"
                      f"[1:v]scale={TARGET_W}:{TARGET_H}[overlay];"
                      f"[base][overlay]overlay=0:0[vout];"
                      f"{a_complex('[0:a]')}[aout]")
                cmd += ["-filter_complex", fc, "-map", "[vout]", "-map", "[aout]"] + v_common + a_out + [out_path]
                return cmd, br_k, get_duration_seconds(input_path) or 0

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

            if hook_type == "Image overlay":
                cmd = ffmpeg_prefix(use_hw_decode) + ["-i", input_path, "-loop", "1", "-t", f"{hook_dur}", "-i", hook_img_path]
                fc = (f"[0:v]{base_vf}[base];"
                      f"[1:v][base]scale2ref=w=iw:h=ih[img][b2];"
                      f"[b2][img]overlay=(W-w)/2:(H-h)/2:enable='between(t,0,{hook_dur})'[vout];"
                      f"{a_complex('[0:a]')}[aout]")
                cmd += ["-filter_complex", fc, "-map", "[vout]", "-map", "[aout]"] + v_common + a_out + [out_path]
                return cmd, br_k, get_duration_seconds(input_path) or 0

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

            if hook_type == "Video overlay":
                cmd = ffmpeg_prefix(use_hw_decode) + ["-i", input_path, "-i", hook_vid_path]
                tnorm = (f"scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=decrease,"
                         f"pad={TARGET_W}:{TARGET_H}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p,fps={out_fps}")
                fc = (f"[0:v]{base_vf}[base];[1:v]{tnorm}[hv];"
                      f"[base][hv]overlay=(W-w)/2:(H-h)/2:enable='between(t,0,{hook_dur})'[vout];"
                      f"{a_complex('[0:a]')}[aout]")
                cmd += ["-filter_complex", fc, "-map", "[vout]", "-map", "[aout]"] + v_common + a_out + [out_path]
                return cmd, br_k, get_duration_seconds(input_path) or 0

            cmd = (ffmpeg_prefix(use_hw_decode)
                   + ["-i", input_path, "-vf", base_vf, "-r", str(out_fps),
                      "-map", "0:v:0", "-map", "0:a?"]
                   + v_common + a_simple() + [out_path])
            return cmd, br_k, get_duration_seconds(input_path) or 0
        except Exception as e:
            st.error(f"build_ffmpeg_cmd error: {e}\n{traceback.format_exc()}")
            return None, None, None

    with st.expander("⚙️ Options", expanded=True):
        col_a, col_b = st.columns(2)
        with col_a:
            use_ultra_stable = st.checkbox("Mode ultra-stable (libx264/veryfast)", value=True)
            use_hw_decode = st.checkbox("Hardware decode (Videotoolbox)", value=False)
            zoom_mode = st.radio("Creative zoom mode", ["zoom + crop", "zoom inverse + pad", "aucun"], index=0)
        with col_b:
            selected_variants = st.multiselect("Variants to generate", options=["1","2","3","4","5"],
                                                default=["1","2","3","4"],
                                                help="1=Base | 2=+Intro+Hook | 3=+Pitch+1% | 4=+Pitch-1% | 5=Overlay")
            hook_type = st.selectbox("Hook type (V2/V3/V4)", ["None", "Image overlay", "Video prepend", "Video overlay"])

    with st.expander("🎣 Hook & Overlay files"):
        hook_img = hook_vid = overlay_img = None
        hook_keep_audio = False
        if hook_type == "Image overlay":
            hook_img = st.file_uploader("Hook image (PNG/JPG)", type=["png","jpg","jpeg"], key="vg_hook_img")
        elif hook_type in ("Video prepend", "Video overlay"):
            hook_vid = st.file_uploader("Hook video (mp4/mov)", type=["mp4","mov","m4v"], key="vg_hook_vid")
            if hook_type == "Video prepend":
                hook_keep_audio = st.checkbox("Keep hook audio (prepend)", value=True)
        overlay_img = st.file_uploader("Variant 5 — Overlay/Border PNG (transparent)", type=["png"], key="vg_overlay")

    st.markdown("### 📥 Videos to process")
    videos = st.file_uploader("Drop one or more videos", type=["mp4","mov","m4v"],
                              accept_multiple_files=True, key="vg_videos")
    st.markdown("---")
    run_btn = st.button("🚀 Generate Selected Variants", type="primary")

    if run_btn and videos:
        if not selected_variants:
            st.error("Select at least one variant.")
        else:
            try:
                all_variants_def = {
                    "1": {"name":"1","hook_dur":0.3,"add_intro":False,"intro_sec":0.0,"audio":"normal"},
                    "2": {"name":"2","hook_dur":0.1,"add_intro":True,"intro_sec":0.01,"audio":"normal"},
                    "3": {"name":"3","hook_dur":0.1,"add_intro":True,"intro_sec":0.01,"audio":"pitch +1%"},
                    "4": {"name":"4","hook_dur":0.1,"add_intro":True,"intro_sec":0.01,"audio":"pitch -1%"},
                    "5": {"name":"5","hook_dur":0.3,"add_intro":False,"intro_sec":0.0,"audio":"normal"},
                }
                variants = [all_variants_def[v] for v in selected_variants]
                total_tasks = len(videos) * len(variants)
                completed_tasks = 0
                overall_progress = st.progress(0)
                overall_status = st.empty()
                overall_status.markdown(f"### {len(videos)} video(s) × {len(variants)} variant(s) = **{total_tasks} tasks**")
                all_generated_files = []
                hook_img_path = hook_vid_path = overlay_img_path = None
                if hook_type == "Image overlay" and hook_img:
                    with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(hook_img.name)[1]) as f:
                        f.write(hook_img.read()); hook_img_path = f.name
                if hook_type in ("Video prepend","Video overlay") and hook_vid:
                    with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(hook_vid.name)[1]) as f:
                        f.write(hook_vid.read()); hook_vid_path = f.name
                if overlay_img:
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as f:
                        f.write(overlay_img.read()); overlay_img_path = f.name

                for video_idx, file in enumerate(videos):
                    st.markdown(f"---\n## 🎬 Video {video_idx+1}/{len(videos)}: **{file.name}**")
                    input_path = None
                    try:
                        with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(file.name)[1]) as tmp:
                            tmp.write(file.read()); input_path = tmp.name
                    except Exception as e:
                        st.error(f"Failed to save input: {e}"); continue
                    base_name = os.path.splitext(os.path.basename(file.name))[0]
                    for variant_idx, variant in enumerate(variants):
                        try:
                            out_name = f"{base_name}_{variant['name']}.mp4"
                            out_tmp = os.path.join(tempfile.gettempdir(), out_name)
                            overall_status.markdown(f"Video **{video_idx+1}/{len(videos)}** • Variant **{variant['name']}** • Progress: **{completed_tasks}/{total_tasks}**")
                            st.markdown(f"### ▶️ Variant {variant['name']}")
                            cmd, br, est_total = build_ffmpeg_cmd(
                                input_path, out_tmp, variant_name=variant["name"],
                                use_hw_decode=use_hw_decode, use_ultra_stable=use_ultra_stable,
                                hook_type=hook_type, hook_dur=variant["hook_dur"],
                                hook_img_path=hook_img_path, hook_vid_path=hook_vid_path,
                                hook_keep_audio=hook_keep_audio, zoom_mode=zoom_mode,
                                audio_mode=variant["audio"], add_blank_intro=variant["add_intro"],
                                blank_intro_sec=variant["intro_sec"], overlay_img_path=overlay_img_path)
                            if cmd is None:
                                st.error(f"Failed to build command for variant {variant['name']}")
                                completed_tasks += 1; overall_progress.progress(completed_tasks / total_tasks); continue
                            pbar = st.progress(0); ptxt = st.empty()
                            log_exp = st.expander(f"FFmpeg logs — variant {variant['name']}", expanded=False)
                            log_box = log_exp.empty()
                            def prog_cb(p, _pb=pbar, _pt=ptxt): _pb.progress(p); _pt.markdown(f"Progress: **{p}%**")
                            def log_cb(line, _lb=log_box): _lb.code(line, language="bash")
                            rc = run_ffmpeg_with_progress(cmd, est_total or 0, progress_cb=prog_cb, log_cb=log_cb)
                            if rc == 0:
                                st.success(f"✅ Variant {variant['name']} done → {out_name}")
                                all_generated_files.append({"path": out_tmp, "name": out_name})
                            else:
                                st.error(f"FFmpeg error code {rc} for variant {variant['name']}")
                            completed_tasks += 1; overall_progress.progress(completed_tasks / total_tasks)
                        except Exception as e:
                            st.error(f"Error on variant {variant['name']}: {e}")
                            completed_tasks += 1; overall_progress.progress(completed_tasks / total_tasks)
                    if input_path:
                        try: os.unlink(input_path)
                        except Exception: pass

                st.markdown("---\n# 📦 Done")
                if all_generated_files:
                    zip_buffer = BytesIO()
                    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                        for fi in all_generated_files:
                            try: zf.write(fi["path"], fi["name"])
                            except Exception as e: st.warning(f"Could not add {fi['name']}: {e}")
                    zip_buffer.seek(0)
                    st.success(f"🎉 {len(all_generated_files)} variant(s) generated!")
                    st.balloons()
                    st.download_button(label=f"⬇️ Download all variants ({len(all_generated_files)} files)",
                                       data=zip_buffer,
                                       file_name=f"TikTok_Variants_{datetime.datetime.now().strftime('%Y%m%d_%H%M')}.zip",
                                       mime="application/zip", key="vg_download_zip")
                    for fi in all_generated_files:
                        try: os.unlink(fi["path"])
                        except Exception: pass
                    for p in [hook_img_path, hook_vid_path, overlay_img_path]:
                        if p:
                            try: os.unlink(p)
                            except Exception: pass
                else:
                    st.error("No videos were successfully generated.")
            except Exception as e:
                st.error(f"FATAL: {e}\n{traceback.format_exc()}")
    elif not videos:
        st.info("Upload videos above, then click **Generate Selected Variants**.")


# ──────────────────────────────────────────────────────────────────────
# TAB: REFRESH METADATA
# ──────────────────────────────────────────────────────────────────────
def render_metadata():
    st.header("🔄 Refresh Metadata")
    st.markdown("Upload videos you already downloaded from TikTok and re-export them with "
                "**brand-new metadata + a unique digital fingerprint**.")

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
    RM_IPHONE_PROFILES = [p for p in RM_DEVICE_PROFILES if p["make"] == "Apple"]

    RM_SAUDI_LOCATIONS = [
        ("Riyadh",24.7136,46.6753),("Jeddah",21.4858,39.1925),("Mecca",21.3891,39.8579),
        ("Medina",24.5247,39.5692),("Dammam",26.4207,50.0888),("Khobar",26.2172,50.1971),
        ("Dhahran",26.2361,50.0393),("Taif",21.2703,40.4158),("Abha",18.2164,42.5053),
        ("Khamis Mushait",18.3000,42.7333),("Tabuk",28.3998,36.5715),("Hail",27.5114,41.7208),
        ("Buraidah",26.3260,43.9750),("Unaizah",26.0844,43.9935),("Najran",17.4933,44.1277),
        ("Jazan",16.8892,42.5611),("Yanbu",24.0895,38.0618),("Al Ahsa",25.3833,49.5856),
        ("Jubail",27.0046,49.6225),("Qatif",26.5196,50.0078),("Hafr Al-Batin",28.4342,45.9601),
        ("Al Kharj",24.1556,47.3350),("Sakaka",29.9697,40.2064),("Arar",30.9753,41.0381),
        ("Al Bahah",20.0129,41.4677),
    ]
    RM_KSA_TZ = datetime.timedelta(hours=3)
    RM_INTENSITY = {
        "Light":  {"crop":(0.985,0.995),"bright":(-0.012,0.012),"contrast":(0.99,1.01),"sat":(0.98,1.02),"noise":(1,2),"pitch":(0.997,1.003)},
        "Medium": {"crop":(0.965,0.985),"bright":(-0.02,0.02),"contrast":(0.97,1.03),"sat":(0.95,1.05),"noise":(2,4),"pitch":(0.99,1.01)},
        "Strong": {"crop":(0.94,0.965),"bright":(-0.035,0.035),"contrast":(0.94,1.06),"sat":(0.90,1.10),"noise":(3,6),"pitch":(0.98,1.02)},
    }

    def rm_random_riyadh_datetime(days_ago_max=10):
        now_riyadh = datetime.datetime.utcnow() + RM_KSA_TZ
        days_ago = random.randint(0, days_ago_max)
        base_date = (now_riyadh - datetime.timedelta(days=days_ago)).date()
        hour = random.randint(8, 23)
        minute = random.randint(0, 59)
        second = random.randint(0, 59)
        local_dt = datetime.datetime.combine(base_date, datetime.time(hour, minute, second))
        utc_dt = local_dt - RM_KSA_TZ
        return local_dt, utc_dt

    def rm_random_metadata_args(device_profile, city_name, lat, lon, days_ago_max=10):
        lat += random.uniform(-0.04, 0.04)
        lon += random.uniform(-0.04, 0.04)
        local_dt, utc_dt = rm_random_riyadh_datetime(days_ago_max)
        creation_time = utc_dt.strftime("%Y-%m-%dT%H:%M:%S.000000Z")
        qt_creation = local_dt.strftime("%Y-%m-%dT%H:%M:%S+0300")
        iso6709 = f"{lat:+.4f}{lon:+.4f}+000.000/"
        unique_id = uuid.uuid4().hex
        args = ["-map_metadata","-1","-map_chapters","-1",
                "-metadata",f"creation_time={creation_time}",
                "-metadata",f"com.apple.quicktime.make={device_profile['make']}",
                "-metadata",f"com.apple.quicktime.model={device_profile['model']}",
                "-metadata",f"com.apple.quicktime.software={device_profile['software']}",
                "-metadata",f"com.apple.quicktime.creationdate={qt_creation}",
                "-metadata",f"com.apple.quicktime.location.ISO6709={iso6709}",
                "-metadata","com.apple.quicktime.location.accuracy.horizontal=5.000000",
                "-metadata",f"com.apple.quicktime.content.identifier={unique_id}",
                "-metadata",f"encoder=HW_{unique_id[:10]}",
                "-metadata","title=","-metadata","comment=","-metadata","description="]
        info = {"device":f"{device_profile['make']} {device_profile['model']}",
                "software":device_profile["software"],
                "location":f"{city_name}, Saudi Arabia",
                "date":local_dt.strftime("%Y-%m-%d %H:%M") + " (KSA time)",
                "id":unique_id[:12]}
        return args, info

    def rm_build_visual_filter(intensity):
        r = RM_INTENSITY[intensity]
        crop = random.uniform(*r["crop"])
        vf = f"crop=iw*{crop:.4f}:ih*{crop:.4f},scale=trunc(iw/{crop:.4f}/2)*2:trunc(ih/{crop:.4f}/2)*2"
        bright = random.uniform(*r["bright"])
        contrast = random.uniform(*r["contrast"])
        sat = random.uniform(*r["sat"])
        vf += f",eq=brightness={bright:.4f}:contrast={contrast:.4f}:saturation={sat:.4f}"
        noise = random.randint(*r["noise"])
        vf += f",noise=alls={noise}:allf=t+u"
        return vf

    def rm_build_audio_filter(intensity):
        r = RM_INTENSITY[intensity]
        pitch = random.uniform(*r["pitch"])
        return f"asetrate=44100*{pitch:.5f},aresample=44100,atempo={1/pitch:.5f}"

    def rm_random_filename(ext="mp4"):
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
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
        cmd += ["-c:v","libx264","-preset","veryfast","-b:v",f"{target_bitrate_k}k",
                "-maxrate",f"{int(target_bitrate_k*1.15)}k","-bufsize",f"{int(target_bitrate_k*2)}k",
                "-c:a","aac","-b:a","128k","-movflags","+faststart",output_path]
        return cmd, meta_info

    # UI
    st.subheader("Step 1 — Upload Videos")
    rm_files = st.file_uploader("Upload one or more videos", type=["mp4","mov","avi","mkv","webm"],
                                accept_multiple_files=True, key="rm_files")

    if "rm_device" not in st.session_state:
        st.session_state.rm_device = random.choice(RM_IPHONE_PROFILES)
    rm_dev_col1, rm_dev_col2 = st.columns([4, 1])
    with rm_dev_col1:
        _dev = st.session_state.rm_device
        st.markdown(f"📱 **Device for this batch:** {_dev['make']} {_dev['model']} (`{_dev['software']}`)")
    with rm_dev_col2:
        if st.button("🔁 Re-roll device", use_container_width=True):
            st.session_state.rm_device = random.choice(RM_DEVICE_PROFILES)
            st.rerun()

    st.subheader("Step 2 — Refresh Options")
    rm_col1, rm_col2, rm_col3 = st.columns(3)
    with rm_col1:
        rm_intensity = st.select_slider("Visual change intensity", options=["Light","Medium","Strong"], value="Medium")
    with rm_col2:
        rm_do_visual = st.checkbox("Apply visual micro-changes", value=True)
        rm_do_audio = st.checkbox("Apply audio pitch micro-shift", value=True)
    with rm_col3:
        rm_bitrate = st.slider("Output bitrate (kbps)", 2000, 6000, 4000, step=250)

    st.info("ℹ️ Same device for all videos • Different Saudi city per video • Realistic KSA time (UTC+3)")

    st.subheader("Step 3 — Refresh")
    rm_go = st.button("🔄 Refresh Metadata for All Videos", use_container_width=True, disabled=not rm_files)

    if rm_go and rm_files:
        rm_results = []
        overall = st.progress(0)
        overall_txt = st.empty()
        total = len(rm_files)
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
                days_ago_max = max(1, min(10, total))
                cmd, meta_info = rm_build_cmd(in_path, out_path, rm_intensity, rm_do_visual, rm_do_audio, rm_bitrate,
                                              rm_device, city_name, city_lat, city_lon, days_ago_max)
                dur = get_duration_seconds(in_path) or 0
                pbar = st.progress(0); ptxt = st.empty()
                log_exp = st.expander(f"FFmpeg logs — {rm_file.name}", expanded=False)
                log_box = log_exp.empty()
                def prog_cb(p, _pb=pbar, _pt=ptxt): _pb.progress(p); _pt.markdown(f"Progress: **{p}%**")
                def log_cb(line, _lb=log_box): _lb.code(line, language="bash")
                rc = run_ffmpeg_with_progress(cmd, dur, progress_cb=prog_cb, log_cb=log_cb)
                if rc == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                    st.success(f"✅ `{rm_file.name}` → `{out_name}`")
                    st.markdown(f"&nbsp;&nbsp;📱 **{meta_info['device']}** ({meta_info['software']}) • 📍 {meta_info['location']} • 🗓️ {meta_info['date']} • 🆔 `{meta_info['id']}`")
                    rm_results.append({"path": out_path, "name": out_name})
                else:
                    st.error(f"❌ FFmpeg error (code {rc}) for `{rm_file.name}`")
            except Exception as e:
                st.error(f"Error processing `{rm_file.name}`: {e}")
            finally:
                try: os.unlink(in_path)
                except Exception: pass
            overall.progress((idx + 1) / total)

        st.markdown("---")
        if rm_results:
            zip_buf = BytesIO()
            with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for r in rm_results:
                    try: zf.write(r["path"], r["name"])
                    except Exception: pass
            zip_buf.seek(0)
            st.success(f"🎉 {len(rm_results)}/{total} video(s) refreshed!")
            st.balloons()
            st.download_button(label=f"⬇️ Download Refreshed Videos ({len(rm_results)} files)",
                               data=zip_buf,
                               file_name=f"Refreshed_Videos_{datetime.datetime.now().strftime('%Y%m%d_%H%M')}.zip",
                               mime="application/zip", key="rm_download_zip")
            for r in rm_results:
                try: os.unlink(r["path"])
                except Exception: pass
        else:
            st.error("No videos were successfully refreshed.")


# ──────────────────────────────────────────────────────────────────────
# TAB: FIND SIMILAR
# ──────────────────────────────────────────────────────────────────────
def render_find_similar():
    st.header("🔍 Find Similar Videos on TikTok")
    st.markdown("Upload an image and find **visually similar TikTok videos** using Google Lens.")

    db = load_db()
    serpapi_key = db.get("settings", {}).get("serpapi_key", "")

    if not serpapi_key:
        st.warning("⚠️ SerpAPI key not configured. Ask the admin to set it in the **Admin Dashboard**.")
        st.stop()

    fs_image = st.file_uploader("Upload an image", type=["png","jpg","jpeg","webp","bmp"], key="fs_image")
    if fs_image:
        st.image(fs_image, caption="Your image", width=300)

    fs_go = st.button("🔍 Find Similar on TikTok", use_container_width=True, disabled=not fs_image)

    def fs_upload_temp_image(image_bytes, filename):
        try:
            resp = requests.post("https://tmpfiles.org/api/v1/upload",
                                 files={"file": (filename, image_bytes)}, timeout=30)
            if resp.status_code == 200:
                url = resp.json().get("data", {}).get("url", "")
                if url:
                    return url.replace("tmpfiles.org/", "tmpfiles.org/dl/")
        except Exception: pass
        try:
            resp = requests.post("https://file.io", files={"file": (filename, image_bytes)}, timeout=30)
            if resp.status_code == 200:
                return resp.json().get("link", "")
        except Exception: pass
        return ""

    def fs_google_lens_search(image_url, api_key):
        params = {"engine": "google_lens", "url": image_url, "api_key": api_key}
        resp = requests.get("https://serpapi.com/search.json", params=params, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        tiktok_results = []
        for match in data.get("visual_matches", []):
            link = match.get("link", "")
            if "tiktok.com" in link:
                tiktok_results.append({"title": match.get("title","TikTok Video"), "link": link,
                                       "thumbnail": match.get("thumbnail",""), "snippet": match.get("snippet","")})
        for item in data.get("text_results", []):
            link = item.get("link", "")
            if "tiktok.com" in link:
                tiktok_results.append({"title": item.get("title","TikTok Video"), "link": link,
                                       "thumbnail": item.get("thumbnail",""), "snippet": item.get("snippet","")})
        seen = set()
        unique = []
        for r in tiktok_results:
            if r["link"] not in seen:
                seen.add(r["link"]); unique.append(r)
        return unique, data

    if fs_go and fs_image:
        with st.spinner("Uploading image..."):
            image_url = fs_upload_temp_image(fs_image.getvalue(), fs_image.name)
        if not image_url:
            st.error("Failed to upload image. Try again.")
        else:
            with st.spinner("Searching for similar TikTok content..."):
                try:
                    results, raw_data = fs_google_lens_search(image_url, serpapi_key)
                except Exception as e:
                    st.error(f"Search failed: {e}"); results = []; raw_data = {}

            if results:
                st.success(f"Found **{len(results)}** similar TikTok video(s)!")
                for i, r in enumerate(results):
                    col_thumb, col_info = st.columns([1, 3])
                    with col_thumb:
                        if r["thumbnail"]:
                            try: st.image(r["thumbnail"], width=150)
                            except Exception: st.markdown("🎬")
                        else:
                            st.markdown("🎬")
                    with col_info:
                        st.markdown(f"**{r['title']}**")
                        if r["snippet"]:
                            st.caption(r["snippet"])
                        st.markdown(f"[🔗 Open on TikTok]({r['link']})")
                        vid_match = re.search(r"/video/(\d+)", r["link"])
                        if vid_match:
                            vid_id = vid_match.group(1)
                            if st.button(f"⬇️ Download", key=f"fs_dl_{i}_{vid_id}"):
                                with st.spinner(f"Downloading {vid_id}..."):
                                    dl_dir = tempfile.mkdtemp()
                                    dl_out = os.path.join(dl_dir, f"{vid_id}.%(ext)s")
                                    dl_cmd = [YTDLP,"-f","bestvideo+bestaudio/best","--merge-output-format","mp4",
                                              "--no-warnings","--no-check-certificate","--retries","5","-o",dl_out,r["link"]]
                                    dl_result = subprocess.run(dl_cmd, capture_output=True, timeout=240)
                                    if dl_result.returncode == 0:
                                        for fname in os.listdir(dl_dir):
                                            if fname.startswith(vid_id):
                                                fpath = os.path.join(dl_dir, fname)
                                                with open(fpath, "rb") as vf:
                                                    st.download_button("💾 Save Video", data=vf.read(),
                                                                       file_name=fname, mime="video/mp4",
                                                                       key=f"fs_save_{i}_{vid_id}")
                                                break
                                    else:
                                        st.warning("Download failed — video may be private.")
                    st.markdown("---")
            else:
                st.warning("No similar TikTok videos found.")
                all_matches = raw_data.get("visual_matches", [])
                if all_matches:
                    with st.expander(f"{len(all_matches)} results on other sites"):
                        for m in all_matches[:10]:
                            st.markdown(f"- [{m.get('title','Result')}]({m.get('link','#')})")
    elif not fs_image:
        st.info("Upload an image above to find similar TikTok videos.")


# ──────────────────────────────────────────────────────────────────────
# TAB: ADMIN DASHBOARD
# ──────────────────────────────────────────────────────────────────────
def render_admin():
    st.header("⚙️ Admin Dashboard")
    db = load_db()

    # ── API Settings ──────────────────────────────────────────────
    st.subheader("🔑 API Settings")
    with st.form("admin_settings_form"):
        serpapi_key = st.text_input("SerpAPI Key (for Find Similar)",
                                    value=db.get("settings", {}).get("serpapi_key", ""),
                                    type="password",
                                    help="Get free key at https://serpapi.com/")
        settings_btn = st.form_submit_button("💾 Save API Key", use_container_width=True)
    if settings_btn:
        if "settings" not in db:
            db["settings"] = {}
        db["settings"]["serpapi_key"] = serpapi_key
        save_db(db)
        st.success("✅ API key saved!")

    st.markdown("---")

    # ── Current Users Table ───────────────────────────────────────
    st.subheader("👥 All Users")
    users = db["users"]
    user_data_table = []
    for uid, u in users.items():
        user_data_table.append({
            "Name": u["name"],
            "Email": u["email"],
            "Role": u["role"],
            "Access": ", ".join(FEATURE_LABELS.get(p, p) for p in u["permissions"]),
            "Created": u.get("created", "N/A"),
        })
    st.dataframe(user_data_table, use_container_width=True, hide_index=True)

    st.markdown("---")

    # ── Add New User ──────────────────────────────────────────────
    st.subheader("➕ Add New User")
    with st.form("admin_add_user_form"):
        au_col1, au_col2 = st.columns(2)
        with au_col1:
            new_name = st.text_input("Full Name", key="admin_new_name")
            new_email = st.text_input("Email (used for login)", key="admin_new_email")
        with au_col2:
            new_pw = st.text_input("Password", type="password", key="admin_new_pw")
            new_perms = st.multiselect("Grant access to:",
                                        options=list(FEATURE_LABELS.keys()),
                                        format_func=lambda x: FEATURE_LABELS[x],
                                        default=["downloader"],
                                        key="admin_new_perms")
        add_btn = st.form_submit_button("➕ Add User", use_container_width=True)

    if add_btn:
        if not new_name or not new_email or not new_pw:
            st.error("All fields are required.")
        elif any(u["email"] == new_email for u in users.values()):
            st.error(f"User with email '{new_email}' already exists.")
        else:
            new_uid = f"user_{uuid.uuid4().hex[:8]}"
            db["users"][new_uid] = {
                "name": new_name,
                "email": new_email,
                "password_hash": hash_pw(new_pw),
                "role": "user",
                "permissions": new_perms,
                "created": datetime.datetime.now().strftime("%Y-%m-%d"),
            }
            save_db(db)
            st.success(f"✅ User **{new_name}** ({new_email}) added!")
            st.rerun()

    st.markdown("---")

    # ── Edit / Remove User ────────────────────────────────────────
    st.subheader("✏️ Edit / Remove User")
    non_admin_users = {uid: u for uid, u in users.items() if u["role"] != "admin"}
    if non_admin_users:
        edit_uid = st.selectbox("Select user to edit:",
                                options=list(non_admin_users.keys()),
                                format_func=lambda x: f"{users[x]['name']} ({users[x]['email']})",
                                key="admin_edit_select")
        edit_user = users[edit_uid]

        with st.form("admin_edit_user_form"):
            edit_perms = st.multiselect("Permissions:",
                                        options=list(FEATURE_LABELS.keys()),
                                        format_func=lambda x: FEATURE_LABELS[x],
                                        default=edit_user["permissions"],
                                        key="admin_edit_perms")
            edit_new_pw = st.text_input("New password (leave blank to keep current)",
                                        type="password", key="admin_edit_pw")
            col_save, col_del = st.columns(2)
            with col_save:
                save_btn = st.form_submit_button("💾 Save Changes")
            with col_del:
                del_btn = st.form_submit_button("🗑️ Delete User")

        if save_btn:
            db["users"][edit_uid]["permissions"] = edit_perms
            if edit_new_pw:
                db["users"][edit_uid]["password_hash"] = hash_pw(edit_new_pw)
            save_db(db)
            st.success(f"✅ Updated {edit_user['name']}")
            st.rerun()
        if del_btn:
            del db["users"][edit_uid]
            save_db(db)
            st.success(f"🗑️ Deleted {edit_user['name']}")
            st.rerun()
    else:
        st.info("No users added yet. Use the form above to add one.")

    st.markdown("---")

    # ── Backup & Restore ─────────────────────────────────────────
    st.subheader("💾 Backup & Restore")
    bk_col1, bk_col2 = st.columns(2)
    with bk_col1:
        st.markdown("**Export** — download all users & settings as a backup file.")
        db_json = json.dumps(db, indent=2)
        st.download_button("⬇️ Download Backup (JSON)", data=db_json,
                           file_name=f"tiktok_studio_backup_{datetime.datetime.now().strftime('%Y%m%d_%H%M')}.json",
                           mime="application/json", key="admin_backup_dl")
    with bk_col2:
        st.markdown("**Import** — restore from a previously exported backup.")
        restore_file = st.file_uploader("Upload backup JSON", type=["json"], key="admin_restore_file")
        if restore_file:
            try:
                restore_data = json.loads(restore_file.getvalue().decode())
                if "users" in restore_data:
                    if st.button("✅ Restore This Backup", use_container_width=True, key="admin_restore_btn"):
                        save_db(restore_data)
                        st.success("✅ Database restored!")
                        st.rerun()
                else:
                    st.error("Invalid backup — missing 'users' key.")
            except Exception as e:
                st.error(f"Failed to parse: {e}")


# ══════════════════════════════════════════════════════════════════════
# RENDER ACTIVE TABS
# ══════════════════════════════════════════════════════════════════════
TAB_RENDERERS = {
    "downloader": render_downloader,
    "variants": render_variants,
    "metadata": render_metadata,
    "find_similar": render_find_similar,
    "admin": render_admin,
}

for i, key in enumerate(tab_keys):
    with tabs[i]:
        renderer = TAB_RENDERERS.get(key)
        if renderer:
            renderer()
