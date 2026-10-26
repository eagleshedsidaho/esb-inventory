# ESB Inventory Mirror

A nightly, static mirror of the [Old Hickory Buildings Eagle, ID dealer
inventory](https://oldhickorybuildings.com/locations/eagle-idaho/) for Eagle Sheds &
Buildings, published as an embeddable widget on GitHub Pages.

No server, no database, no login. A GitHub Actions job scrapes the dealer page, caches
the photos, and commits static files to `docs/`. GitHub Pages serves them and the ESB
AI Studio inventory page shows them in an iframe.

```
Old Hickory dealer page ──(nightly GitHub Action)──▶ docs/inventory.json
  + each new shed's detail page                       docs/images/*.jpg
                                                      docs/widget.html ──▶ iframe on eagleshedsidaho.com
```

## Repository layout

| Path | What it is |
|---|---|
| `scraper/run.py` | The nightly pipeline: robots.txt → listing → safety rail → detail pages → photos → diff → publish |
| `scraper/scrape.py` | Fetching (throttled, robots-aware) and HTML parsing |
| `scraper/normalize.py` | Turns scraped strings into the canonical shed record |
| `scraper/images.py` | Photo cache: validate, resize to 1600px, strip metadata, prune sold sheds |
| `scraper/diff.py` | added / removed / price_changed diff and the safety rail |
| `scraper/build_widget.py` | Inlines `widget/` into the single-file `docs/widget.html` |
| `scraper/fetch_fixture.py` | Re-downloads the test fixtures from the live site |
| `notifications/send_summary.py` | Plain-text change email over SMTP |
| `widget/` | Widget source: `index.html`, `styles.css`, `render.js`, plus `embed-test.html` |
| `docs/` | **Published by GitHub Pages**: `inventory.json`, `widget.html`, `images/` |
| `tests/` | pytest suite and the frozen HTML fixtures |
| `.github/workflows/scrape.yml` | Nightly scrape (schedule disabled until enabled by hand) |
| `.github/workflows/ci.yml` | ruff + black + pytest + stale-widget check on every push/PR |

## One-time setup

1. **Create the repo.** On GitHub, create a public repo named `esb-inventory` (the URLs
   below assume that name) and push this folder to its `main` branch.
2. **Turn on GitHub Pages.** Repo → **Settings → Pages** → *Build and deployment* →
   Source **Deploy from a branch** → Branch **main**, folder **/docs** → **Save**.
   After a minute the widget is live at `https://{username}.github.io/esb-inventory/widget.html`.
3. **Add the SMTP secrets.** Repo → **Settings → Secrets and variables → Actions → New
   repository secret**, once each for:

   | Secret | Google Workspace value |
   |---|---|
   | `SMTP_HOST` | `smtp.gmail.com` |
   | `SMTP_USER` | the sending Workspace address |
   | `SMTP_PASS` | an [app password](https://support.google.com/accounts/answer/185833) for that address (not its normal password) |
   | `SMTP_FROM` | e.g. `ESB Inventory <that-address@eagleshedsidaho.com>` |

   Optional: `SMTP_PORT` (default `587`) and `SMTP_TO` (default
   `info@eagleshedsidaho.com`). To use them, also add them to the `env:` block of the
   *Email change summary* step in `scrape.yml`.
4. **If `main` is branch-protected,** allow GitHub Actions to push to it, or the commit
   step will fail.
5. **Do a manual run.** **Actions → Nightly inventory scrape → Run workflow**. Check that
   it goes green and that the email arrives when there are changes.
6. **Enable the nightly schedule** (after checking the dealer agreement): in
   `.github/workflows/scrape.yml`, uncomment the three `schedule:` lines and push. It runs
   at 06:00 America/Denver all year: GitHub cron is UTC-only, so there's one trigger per
   daylight-saving season and a `gate` job lets the right one through.

## Local development

Requires Python 3.11+.

```bash
python -m venv .venv
```
```bash
.venv/bin/pip install -r requirements.txt     # Windows: .venv\Scripts\pip
```

| Command | Does |
|---|---|
| `python -m scraper.run` | Full live run (fetches Old Hickory, writes `docs/`) |
| `python -m scraper.run --snapshot tests/snapshot.html` | Offline run against the fixture, using only cached details and photos |
| `python -m scraper.build_widget` | Rebuild `docs/widget.html` after editing `widget/` |
| `python -m notifications.send_summary scrape_diff.json --dry-run` | Print the email that would be sent |
| `python -m http.server 8765` | Serve the repo root to preview `http://localhost:8765/docs/widget.html` |
| `ruff check . && black --check . && pytest` | What CI runs |

Opening `docs/widget.html` straight from disk (`file://`) shows the fallback message,
because browsers block `fetch()` on local files. Use the local server above.

## How the nightly job works

1. **robots.txt** is checked before the listing page and before every detail page.
   If it disallows the listing, the run aborts.
2. **One listing fetch.** It parses the ~12 shed cards on the Eagle page.
3. **Safety rail.** If the shed count drops below 50% or more than doubles versus the
   published `inventory.json`, or comes back as zero, nothing is published:
   `docs/last_scrape_warning.json` is written, the run exits with code 3, and the workflow
   fails, so GitHub sends you a notification.
4. **Detail pages** are fetched only for new sheds, sheds still missing siding or roof
   data, or sheds whose main photo changed. That's where siding, roof and gallery photos
   come from.
5. **Photos** are downloaded only when new or changed. Each one is checked to be a real
   JPEG or PNG, turned upright, capped at 1600px, saved as JPEG without metadata, and
   named `{inventory_id}_{n}.jpg`. Photos of sheds no longer listed are deleted.
6. **Diff and publish.** If anything visible changed, the workflow rebuilds the widget and
   commits `docs/` to `main`. If any shed was added, removed or repriced, it also
   emails `info@eagleshedsidaho.com`.

All requests use the User-Agent `ESB-Inventory-Mirror/1.0 (contact:
info@eagleshedsidaho.com)` and are spaced at least 500ms apart.

**Exit codes of `scraper.run`:** `0` OK · `2` robots.txt disallowed the page, or it
couldn't be fetched · `3` safety rail tripped.

### `inventory.json`

```json
{
  "source_url": "https://oldhickorybuildings.com/locations/eagle-idaho/",
  "generated_at": "2026-10-26T13:34:28+00:00",
  "count": 12,
  "sheds": [
    {
      "inventory_id": "01tjw00000j6kkwia3",
      "style": "Barn", "width_ft": 8, "length_ft": 12,
      "siding_color": "Pewter Cast", "roof_type": "Metal", "roof_color": "Black",
      "cash_price_usd": 3140.75, "rto_36mo_usd": 145.41, "rto_60mo_usd": 116.32,
      "rto_84mo_usd": null,
      "oh_detail_url": "https://oldhickorybuildings.com/product/01tjw00000j6kkwia3/",
      "oh_image_urls": ["https://ohbprodinv.s3.us-east-2.amazonaws.com/…"],
      "image_files": ["images/01tjw00000j6kkwia3_0.jpg", "…"],
      "first_seen": "2026-10-26", "last_seen": "2026-10-26"
    }
  ]
}
```

`inventory_id` is the ID from Old Hickory's product URL, which is stable for each shed.
`rto_84mo_usd` stays `null`, because Old Hickory doesn't publish an 84-month term today.
`image_files` lists only photos that were actually cached, and the widget uses these
rather than Old Hickory's S3 URLs.

## Embedding in the ESB AI Studio inventory page

### The URL

```
https://{username}.github.io/esb-inventory/widget.html
```

Replace `{username}` with the GitHub account (user or organization) that owns the repo.
If you named the repo something other than `esb-inventory`, use that name instead. Open
the URL in a browser first: you should see the shed grid on its own.

### The snippet

```html
<iframe
  id="esb-inventory-frame"
  src="https://{username}.github.io/esb-inventory/widget.html"
  title="Sheds in stock at Eagle Sheds &amp; Buildings"
  style="display: block; width: 100%; height: 900px; border: 0"
></iframe>
<script>
  (function () {
    var frame = document.getElementById("esb-inventory-frame");
    var origin = new URL(frame.src).origin;
    window.addEventListener("message", function (event) {
      var data = event.data;
      if (event.origin !== origin || !data || data.type !== "esb-inventory:height") return;
      frame.style.height = data.height + "px";
    });
    frame.addEventListener("load", function () {
      frame.contentWindow.postMessage({ type: "esb-inventory:request-height" }, origin);
    });
  })();
</script>
```

The `<script>` makes the iframe exactly as tall as the widget, so the page scrolls
normally and the iframe never shows its own scrollbar. It grows and shrinks as visitors
use the filters. It only accepts height messages from your GitHub Pages origin. Without
the script, the widget shows in a 900px-tall box that scrolls inside itself, so nothing
is ever cut off.

### Dimensions and scrolling

- **Width: `100%`** of the content column, the same column the current shed cards sit
  in. Don't put the iframe inside another padded box; the widget has its own 8px inset.
  It lays out like the live page: 3 columns at 1024px+ of iframe width, 2 columns at
  640–1023px, 1 column below that.
- **Height: let the script set it.** Measured heights for 12 sheds: about **2,460px** at
  desktop width (3 columns) and about **8,600px** on a phone (1 column). No fixed height
  works for both, which is why the snippet auto-sizes.
- **Scrolling: don't add `scrolling="no"`.** If the script is ever stripped, that would cut
  the widget off at 900px instead of letting it scroll.
- **Background:** the widget is white, matching the white section the cards sit in on
  `/sheds/inventory/`. To place it on the cream page background instead, change
  `--esb-bg` in `widget/styles.css` to `#fcfaf8` and rebuild.

### Adding it in AI Studio

AI Studio builds pages by prompt and renders them with React. HighLevel doesn't document a
"custom HTML" element for AI Studio. In React an inline `<script>` pasted as HTML usually
doesn't run, so ask the AI to build the equivalent instead. In the AI Studio project for
eagleshedsidaho.com, on the `/sheds/inventory/` page, prompt:

> On the /sheds/inventory page, keep the hero section ("What's on the lot right now")
> and replace the shed card grid section below it with an embedded iframe.
> - iframe src: `https://{username}.github.io/esb-inventory/widget.html`
> - title: "Sheds in stock at Eagle Sheds & Buildings"
> - full width of the existing content container, no border, display block, initial
>   height 900px, no `scrolling="no"` and no `sandbox` attribute
> - add a `window` "message" event listener (cleaned up on unmount) that ignores any
>   message whose `event.origin` isn't `https://{username}.github.io`, and when
>   `event.data.type === "esb-inventory:height"` sets the iframe's height to
>   `event.data.height` pixels
> - when the iframe fires `load`, post `{ type: "esb-inventory:request-height" }` to
>   `iframe.contentWindow` with target origin `https://{username}.github.io`
> - keep the section's white background and vertical padding as they are now

Then preview, check the steps below, and **publish** the AI Studio project. Changes don't
go live until you publish.

If you embed it with the standard HighLevel page builder instead, drag a **Custom
HTML / Code** element (the exact label varies by builder version) into a full-width row
and paste the snippet above unchanged. Scripts do run there.

**Check after publishing:**
1. The grid shows with no inner scrollbar, and the page footer sits right below the last card.
2. Click a filter, such as *Cabana*. The page should get shorter with no blank gap.
3. Click *Contact us about this shed*. Your mail app should open a draft to
   info@eagleshedsidaho.com with the inventory ID in the subject.
4. Check it on a phone as well.

**SEO trade-off:** search engines index iframe content as part of the GitHub Pages
URL, not as part of eagleshedsidaho.com. The shed listings will no longer count as text
on ESB's own `/sheds/inventory/` page. Keep the page's own heading and intro copy for SEO.

### Testing the embed locally

`widget/embed-test.html` hosts the same snippet, pointed at the local build. Run
`python -m http.server 8765` from the repo root and open
`http://localhost:8765/widget/embed-test.html`.

## Troubleshooting

### The iframe is blank or shows the "isn't available right now" message

1. **Open the widget URL directly**:
   `https://{username}.github.io/esb-inventory/widget.html`.
   - **404 from GitHub:** Pages isn't serving yet. Check **Settings → Pages** (branch
     `main`, folder `/docs`) and that `docs/widget.html` exists on `main`. The first
     deploy can take a few minutes. **Actions → pages-build-deployment** shows its status.
   - **The fallback message** ("Our live inventory list isn't available right now"):
     `inventory.json` didn't load. Open `…/esb-inventory/inventory.json` directly. If
     that 404s, it was never committed; run the workflow by hand. If it opens but looks
     broken, check the last commit to `docs/inventory.json`.
   - **"No sheds in stock on our lot right now":** `inventory.json` loaded but has zero
     sheds. The safety rail should prevent this, so check recent commits.
2. **Works directly but not on the ESB page:** open the browser's developer tools
   console on the ESB page.
   - `Refused to frame` / `frame-ancestors`: a Content-Security-Policy is blocking it.
     Neither site sends one today (checked 2026-10-26), so check whether a Cloudflare
     or HighLevel setting started adding one.
   - Mixed content: the `src` must start with `https://`.
   - Nothing in the console and the iframe is missing: the AI Studio change wasn't
     published, or the AI dropped the iframe. Re-prompt and republish.
3. **Iframe shows but is cut off or has its own scrollbar:** the height listener isn't
   running. In the ESB page's console, the iframe's `style.height` should be a large
   number like `2460px`, not `900px`. Check that the origin in the listener exactly matches
   `https://{username}.github.io` (no trailing slash, no repo path), and that nothing
   added `scrolling="no"`.

### Photos don't load (grey "Photo coming soon" boxes)

1. Open one directly: `https://{username}.github.io/esb-inventory/images/<id>_0.jpg`,
   using an ID from `inventory.json`.
2. **404:** check that `docs/images/` was committed. The commit step stages it with
   `git add -A docs/images`, so look at the last *Inventory update* commit. GitHub Pages
   paths are case-sensitive, and the files are all lowercase.
3. **A shed has an empty `image_files` list** in `inventory.json`: its downloads failed
   validation or the network request failed. Look for `image … rejected:` warnings in the
   *Scrape Old Hickory* step log. The next run retries automatically.
4. **Only some photos load on a slow phone:** photos lazy-load as you scroll. That's
   expected.

### "Contact us about this shed" does nothing

The button is a `mailto:` link. If the iframe has a `sandbox` attribute, browsers block
it; remove `sandbox`, or include `allow-popups allow-top-navigation-by-user-activation`.
On a computer with no mail app configured, `mailto:` links do nothing. Test on a phone.

### The widget shows yesterday's data

GitHub Pages caches files for about 10 minutes after a deploy, and the deploy itself
takes a minute or two after the commit. The widget always re-checks `inventory.json`
(`cache: no-cache`), so a normal reload after that window shows the new data. Check
**Actions** to see whether the nightly run committed anything. A run with no changes
doesn't commit, so "Inventory updated" only moves forward when something changed.

### The scraper breaks

Start in **Actions → Nightly inventory scrape → the failed run**. The failing step
tells you which case you're in.

**Lint / Format check / Tests failed:** the code on `main` is broken. Nothing was
scraped or published. Fix it locally with `ruff check . && black --check . && pytest`.

**Fail - safety rail tripped** (exit 3): the shed count changed drastically.
1. Download the **last-scrape-warning** artifact from the run page, or read the job
   summary. It shows the reason, both counts, and which sheds would be added or removed.
2. Open the [Eagle dealer page](https://oldhickorybuildings.com/locations/eagle-idaho/)
   and count the sheds yourself.
3. **If the change is real** (big restock, lot sold down), go to **Actions → Nightly
   inventory scrape → Run workflow**, check **override_safety_rail**, and run. It
   publishes once; the next night's run compares against the new count.
4. **If the page looks normal but the scraper got a different count,** Old Hickory
   probably changed their HTML. See *Parser out of date* below.

**Fail - scrape could not run** (exit 2): the step log says which.
- `robots.txt disallows …`: Old Hickory changed robots.txt to block this page. Don't
  work around it; contact Old Hickory.
- `could not fetch …`: the site was down or timed out. It usually clears by the next
  night. If it doesn't, try loading the page in a browser.

**Parser out of date:** the run succeeds, but sheds are missing fields (prices `null`,
no style) or the count is wrong. Old Hickory changed their page markup. To update:

1. Pull the latest `main`, then refresh the fixtures from the live site:
   ```bash
   python -m scraper.fetch_fixture
   ```
   This rewrites `tests/snapshot.html` (the listing) and `tests/detail_snapshot.html`
   (the first shed's detail page), using the same User-Agent, robots check and rate
   limit as the nightly job.
2. Run the tests:
   ```bash
   pytest
   ```
   - Tests that pin values from the old snapshot will fail even when the parser is
     fine: shed count, the exact first/last shed, the style list, and the detail-page
     shed's colors (in `tests/test_parse.py`). Open the new snapshot and update those
     expected values.
   - Tests failing because fields come back `None`, or zero cards are found, mean the
     markup changed. Open `tests/snapshot.html`, find a shed card, and update the CSS
     selectors in `parse_listing()` / `parse_detail()` in `scraper/scrape.py`. The current
     ones are `li.custom-product-item`, `.product-title`, `.product-width`,
     `.product-length`, `.product-pricing .pricing` with `.price-type` / `.price-value`,
     `.product-image a/img`, and on detail pages `.product-details ul li` and
     `img.product-thumbnail[data-large-image]`.
3. Check the result offline:
   ```bash
   python -m scraper.run --snapshot tests/snapshot.html --docs-dir /tmp/esb-check
   ```
4. Run `ruff check . && black --check . && pytest`, then commit the new fixtures and parser
   changes together. CI runs on the push. Then trigger **Run workflow** by hand to confirm
   a live run.

### The email didn't arrive

- The workflow only emails when sheds are added, removed or repriced. A run that only
  picked up new photos or siding details doesn't email. The job summary shows
  *Email sent: true/false*.
- A failed **Email change summary** step names any missing secret. `could not send email`
  with `535` means Google rejected the login: check `SMTP_USER` and regenerate the app
  password. Inventory is committed before the email step runs, so a failed email doesn't
  lose data.
- Check spam, and preview the content locally with `--dry-run`.
