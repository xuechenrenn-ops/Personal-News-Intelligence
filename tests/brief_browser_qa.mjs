// Development-only visual checks against installed Edge. Run with Node 22+.
import { spawn } from "node:child_process";
import { mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

const [, , edge, reportFolder] = process.argv;
const root = resolve(reportFolder);
const profile = await mkdtemp(join(tmpdir(), "pni-edge-"));
const browser = spawn(edge, ["--headless=new", "--remote-debugging-port=0", "--remote-debugging-address=127.0.0.1",
  "--disable-background-networking", "--disable-gpu", "--in-process-gpu", "--disable-extensions",
  "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
  "--no-first-run", `--user-data-dir=${profile}`, "about:blank"],
  { windowsHide: true, stdio: ["ignore", "ignore", "pipe"] });
let diagnostic = "";
browser.stderr.on("data", chunk => { diagnostic = (diagnostic + chunk).slice(-1600); });
let ws;
try {
  let port;
  for (let attempt = 0; attempt < 100 && !port; attempt++) {
    port = (await readFile(join(profile, "DevToolsActivePort"), "utf8").catch(() => "")).split("\n")[0].trim();
    if (!port) await new Promise(resolve => setTimeout(resolve, 100));
  }
  if (!port) throw new Error("Edge did not expose a DevTools port");
  const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  ws = new WebSocket(targets.find(target => target.type === "page").webSocketDebuggerUrl);
  await new Promise((ok, fail) => {
    ws.addEventListener("open", ok, { once: true });
    ws.addEventListener("error", fail, { once: true });
  });
  let seq = 0;
  const pending = new Map();
  ws.addEventListener("close", () => {
    for (const [, fail] of pending.values()) fail(new Error(`Edge disconnected: ${diagnostic}`));
    pending.clear();
  });
  ws.addEventListener("message", ({ data }) => {
    const reply = JSON.parse(data);
    const pair = pending.get(reply.id);
    if (pair) {
      pending.delete(reply.id);
      clearTimeout(pair[2]);
      if (reply.error) pair[1](new Error(reply.error.message));
      else pair[0](reply.result);
    }
  });
  const call = (method, params = {}) => new Promise((ok, fail) => {
    if (ws.readyState !== WebSocket.OPEN) return fail(new Error(`Edge unavailable: ${diagnostic}`));
    const id = ++seq;
    const timeout = setTimeout(() => {
      pending.delete(id);
      fail(new Error(`${method} timed out: ${diagnostic}`));
    }, 8000);
    pending.set(id, [ok, fail, timeout]);
    ws.send(JSON.stringify({ id, method, params }));
  });
  const evaluate = async expression => {
    const response = await call("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
    if (response.exceptionDetails) throw new Error(response.result.description);
    return response.result.value;
  };
  const metrics = [];
  const url = pathToFileURL(join(root, "message-1.html")).href;
  await call("Page.navigate", { url });
  for (const [label, width, color] of [["light", 390, "light"], ["dark", 390, "dark"], ["compact-dark", 320, "dark"]]) {
    await call("Emulation.setDeviceMetricsOverride", { width, height: 844, deviceScaleFactor: 1, mobile: true });
    // This payload has inline colors and no media rules. Simulate the client host
    // by changing its background underneath the unchanged payload.
    for (let attempt = 0; attempt < 50; attempt++) {
      if (await evaluate(`location.href === ${JSON.stringify(url)} && document.querySelectorAll('h2').length > 1`)) break;
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    await evaluate(`document.body.style.colorScheme = ${JSON.stringify(color)}; document.body.style.background = ${JSON.stringify(color === "dark" ? "#222" : "#fff")}; document.fonts.ready.then(() => true)`);
    const state = await evaluate(`(() => {
      const rgb = s => s.match(/[\\d.]+/g).map(Number);
      const lum = rgb => rgb.slice(0, 3).map(c => c / 255).map(c => c <= 0.04045 ? c / 12.92 :
        ((c + 0.055) / 1.055) ** 2.4).reduce((v, c, i) => v + c * [0.2126, 0.7152, 0.0722][i], 0);
      const nodes = [...document.querySelectorAll('p,h2,b,a')];
      const contrasts = nodes.map(node => {
        const fg = lum(rgb(getComputedStyle(node).color));
        let parent = node;
        let bg;
        while (parent) {
          bg = rgb(getComputedStyle(parent).backgroundColor);
          if (bg.length === 3 || bg[3] === 1) break;
          parent = parent.parentElement;
        }
        const light = parent ? lum(bg) : 1;
        return (Math.max(fg, light) + 0.05) / (Math.min(fg, light) + 0.05);
      });
      return { width: innerWidth, content: document.documentElement.scrollWidth,
        overflow: nodes.filter(n => n.getBoundingClientRect().right > innerWidth || n.scrollWidth > n.clientWidth + 1
          && getComputedStyle(n).display === 'block').length,
        minContrast: Math.min(...contrasts), links: [...document.links].every(a => a.protocol === 'https:'),
        articleHeadings: document.querySelectorAll('h2').length - 1 };
    })()`);
    metrics.push({ label, ...state });
    if (state.width !== width || state.content > width || state.overflow ||
        state.minContrast < 4.5 || !state.links || state.articleHeadings !== 5) {
      throw new Error(`Visual assertions failed: ${JSON.stringify(metrics)}`);
    }
    for (const [area, scroll] of [["top", null], ["article", 1], ["news", 4]]) {
      await evaluate(scroll === null ? "scrollTo(0, 0)" :
        `document.querySelectorAll('h2')[${scroll}].scrollIntoView()`);
      const shot = await call("Page.captureScreenshot", { format: "png" });
      await writeFile(join(root, `mobile-${label}-${area}.png`), Buffer.from(shot.data, "base64"));
    }
  }
  await writeFile(join(root, "visual-qa.json"), JSON.stringify(metrics, null, 2));
  console.log(JSON.stringify(metrics));
} finally {
  ws?.close();
  browser.kill();
}
