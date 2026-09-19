/* The "Connect a model" dialog, when the server will not say what is already
 * configured.
 *
 * Reported symptom: the dialog opens with an empty PROVIDER dropdown and no
 * explanation. The cause was a status probe whose failure was caught and
 * discarded, leaving a dialog with nothing to choose from and no way forward
 * — the one state it cannot be talked out of, on the one screen whose job is
 * to fix a broken configuration.
 *
 * A dialog that exists to accept a key must still accept one when the probe
 * fails. These scenarios check exactly that, and that the failure is said out
 * loud rather than swallowed.
 */
const vm = require("vm");
const fs = require("fs");
const path = require("path");
const { run } = require("./dom_harness");

const APP = process.argv[2] || path.join(__dirname, "..", "..", "iris", "app", "static", "app.js");
const SRC = fs.readFileSync(APP, "utf8");
let failures = 0;

function boot(fetchStub) {
  const h = run(null, fetchStub ? { fetch: fetchStub } : undefined);
  vm.createContext(h.ctx);
  try { vm.runInContext(SRC, h.ctx, { filename: "app.js" }); } catch (e) { h.bootError = e; }
  return h;
}

function check(name, ok, detail) {
  if (ok) { console.log("OK   " + name); return; }
  failures++;
  console.log("FAIL " + name + (detail ? " — " + detail : ""));
}

/* The dialog is opened the way a person opens it. Reaching inside the IIFE is
 * not possible, and going through the button is what the bug was about. */
async function openDialog(h) {
  const btn = h.ctx.document.getElementById("btnSetupOpen");
  if (!btn || typeof btn.onclick !== "function") return "no open button wired";
  await btn.onclick();
  return null;
}

function optionValues(html) {
  return [...String(html || "").matchAll(/value="([^"]*)"/g)].map((m) => m[1]);
}

(async () => {
  /* ── 1. the reported failure ──────────────────────────────────────────── */
  {
    const h = boot();                      // default stub: every request fails
    if (h.bootError) { check("boot with a dead server", false, h.bootError.message); }
    else {
      const err = await openDialog(h);
      if (err) check("dialog opens when the probe fails", false, err);
      else {
        const opts = optionValues(h.ctx.document.getElementById("setupProvider").innerHTML);
        check("dropdown is not empty when the probe fails", opts.length >= 3,
              `got ${opts.length} options: ${JSON.stringify(opts)}`);
        check("groq is offered when the probe fails", opts.includes("groq"),
              JSON.stringify(opts));
        const msg = h.ctx.document.getElementById("setupMsg").textContent || "";
        check("the failure is explained, not swallowed", /could not read/i.test(msg),
              `message was ${JSON.stringify(msg)}`);
      }
    }
  }

  /* ── 2. the server answers: its list wins ─────────────────────────────── */
  {
    const payload = {
      configured: false,
      providers: [
        { name: "zeta", label: "Zeta Labs", configured: false, masked: "",
          signup: "https://example.invalid/keys", hint: "starts with z-", note: "Only in this test." },
      ],
      user_name: "", assistant_name: "Iris",
      env_path: "/tmp/.env", env_writable: true, works_without_key: true,
    };
    const h = boot((url) => Promise.resolve({
      ok: true,
      status: 200,
      json: async () => (String(url).includes("/setup/status") ? payload : {}),
    }));
    if (h.bootError) { check("boot with a live server", false, h.bootError.message); }
    else {
      const err = await openDialog(h);
      if (err) check("dialog opens when the probe succeeds", false, err);
      else {
        const opts = optionValues(h.ctx.document.getElementById("setupProvider").innerHTML);
        check("the server's list replaces the fallback", opts.length === 1 && opts[0] === "zeta",
              JSON.stringify(opts));
        const msg = h.ctx.document.getElementById("setupMsg").textContent || "";
        check("no error is shown when nothing failed", msg === "",
              `message was ${JSON.stringify(msg)}`);
      }
    }
  }

  console.log(failures ? `\n${failures} failure(s)` : "\nall setup scenarios passed");
  process.exit(failures ? 1 : 0);
})();
