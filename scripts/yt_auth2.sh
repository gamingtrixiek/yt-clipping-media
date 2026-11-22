#!/data/data/com.termux/files/usr/bin/bash
# Sign-in that also allows posting comments (Google's TV sign-in can't grant that).
# Uses a "Desktop app" OAuth client: Chrome on this tablet sends the approval back to Termux.
set -euo pipefail
REPO="gamingtrixiek/yt-clipping"
KEYS="$HOME/.yt_client"            # kept only inside Termux's private storage
if [ -s "$KEYS" ]; then
  . "$KEYS"; echo "Using your saved Desktop key."
else
  read -rp "Paste the DESKTOP Client ID: " CID
  read -rp "Paste the DESKTOP Client secret: " CSEC
  CID=$(echo "$CID" | tr -d '[:space:]'); CSEC=$(echo "$CSEC" | tr -d '[:space:]')
  ( umask 077; printf 'CID=%q\nCSEC=%q\n' "$CID" "$CSEC" > "$KEYS" )
fi
RT=$(CID="$CID" CSEC="$CSEC" python3 - <<'PY'
import http.server, json, os, subprocess, sys, urllib.parse, urllib.request
cid, sec, port = os.environ["CID"], os.environ["CSEC"], 8765
redir = f"http://127.0.0.1:{port}"
url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
    "client_id": cid, "redirect_uri": redir, "response_type": "code", "access_type": "offline",
    "prompt": "consent",
    "scope": "https://www.googleapis.com/auth/youtube https://www.googleapis.com/auth/youtube.force-ssl"})
got = {}
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        got.update({k: v[0] for k, v in q.items()})
        self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers()
        self.wfile.write(b"<h2>Done - go back to Termux.</h2>" if "code" in got else b"<h2>Not approved.</h2>")
    def log_message(self, *a): pass
srv = http.server.HTTPServer(("127.0.0.1", port), H)
print("\nOpening Chrome. Choose the PS5eXtreme account, tap Continue, tick BOTH boxes, Continue.", file=sys.stderr)
print("If Chrome doesn't open, copy this link into Chrome:\n" + url + "\n", file=sys.stderr)
subprocess.run(["am", "start", "-a", "android.intent.action.VIEW", "-d", url],
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
while "code" not in got and "error" not in got:
    srv.handle_request()
if "code" not in got:
    sys.exit("Google said: " + got.get("error", "no code"))
data = urllib.parse.urlencode({"code": got["code"], "client_id": cid, "client_secret": sec,
                               "redirect_uri": redir, "grant_type": "authorization_code"}).encode()
try:
    tok = json.load(urllib.request.urlopen("https://oauth2.googleapis.com/token", data))
except urllib.error.HTTPError as e:
    sys.exit("Google refused: " + e.read().decode())
if "force-ssl" not in tok.get("scope", ""):
    sys.exit("The 'manage comments' box wasn't ticked - run again and tick both boxes.")
print(tok["refresh_token"])
PY
)
printf %s "$CID"  | gh secret set YT_CLIENT_ID -R "$REPO"
printf %s "$CSEC" | gh secret set YT_CLIENT_SECRET -R "$REPO"
printf %s "$RT"   | gh secret set YT_REFRESH_TOKEN -R "$REPO"
echo "✅ Saved. Comments can now be posted. You can close Termux."
