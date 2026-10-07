import json, subprocess, time, base64, urllib.request, os, tempfile
import websocket

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shots")
os.makedirs(OUT, exist_ok=True)
port = 9333
prof = tempfile.mkdtemp()
p = subprocess.Popen([CHROME, "--headless=new", f"--remote-debugging-port={port}", f"--user-data-dir={prof}",
                      "--window-size=1500,1000", "--hide-scrollbars", "--remote-allow-origins=*", "about:blank"])
try:
    for _ in range(50):
        try:
            tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json"))
            page = [t for t in tabs if t["type"] == "page"][0]
            break
        except Exception:
            time.sleep(0.3)
    ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=60)
    n = 0
    def call(method, **params):
        global n
        n += 1
        ws.send(json.dumps({"id": n, "method": method, "params": params}))
        while True:
            m = json.loads(ws.recv())
            if m.get("id") == n:
                return m.get("result", m)
    def js(expr):
        r = call("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        return r["result"].get("value")

    call("Page.enable")
    call("Emulation.setDeviceMetricsOverride", width=1500, height=1000, deviceScaleFactor=2, mobile=False)
    call("Page.navigate", url="http://localhost:8000/")
    time.sleep(4)
    count = js("document.querySelectorAll('#demo .grid > div').length")
    print("panels:", count)
    names = ["analytics", "revenue", "cost", "vendors", "aging", "monthly"]
    for i in range(count):
        js(f"document.querySelectorAll('#demo .grid > div')[{i}].scrollIntoView({{block:'center'}})")
        time.sleep(1.2)
        r = js(f"(()=>{{const b=document.querySelectorAll('#demo .grid > div')[{i}].getBoundingClientRect();return [b.x,b.y+scrollY,b.width,b.height]}})()")
        shot = call("Page.captureScreenshot", format="png", captureBeyondViewport=True,
                    clip={"x": r[0], "y": r[1], "width": r[2], "height": r[3], "scale": 1})
        path = os.path.join(OUT, f"{i+1}_{names[i] if i < len(names) else i}.png")
        open(path, "wb").write(base64.b64decode(shot["data"]))
        print(path, [round(v) for v in r])
finally:
    p.terminate()
