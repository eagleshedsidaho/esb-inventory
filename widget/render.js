/*
 * ESB inventory widget: loads ./inventory.json and renders filterable shed cards.
 * Plain JS, no dependencies. All shed data is inserted with textContent/attributes,
 * never innerHTML, so nothing in inventory.json can inject markup.
 */
(function () {
  "use strict";

  var CONTACT_EMAIL = "info@eagleshedsidaho.com";
  var FETCH_TIMEOUT_MS = 10000;

  // Square-footage buckets; labels use the common shed sizes at each boundary.
  var SIZE_BUCKETS = [
    { id: "small", label: "Up to 8×12", test: function (sqft) { return sqft <= 96; } },
    { id: "medium", label: "Up to 10×16", test: function (sqft) { return sqft > 96 && sqft <= 160; } },
    { id: "large", label: "10×20 & up", test: function (sqft) { return sqft > 160; } },
  ];

  var PRICE_BUCKETS = [
    { id: "under4", label: "Under $4,000", test: function (p) { return p < 4000; } },
    { id: "4to6", label: "$4,000–$6,000", test: function (p) { return p >= 4000 && p < 6000; } },
    { id: "6to8", label: "$6,000–$8,000", test: function (p) { return p >= 6000 && p < 8000; } },
    { id: "over8", label: "$8,000+", test: function (p) { return p >= 8000; } },
  ];

  var root = document.getElementById("esb-inventory");
  var filtersEl = document.getElementById("esb-filters");
  var statusEl = document.getElementById("esb-status");
  var gridEl = document.getElementById("esb-grid");
  var fallbackEl = document.getElementById("esb-fallback");
  var fallbackMsgEl = document.getElementById("esb-fallback-message");
  var updatedEl = document.getElementById("esb-updated");

  var sheds = [];
  var filters = { style: null, size: null, price: null };

  // ------------------------------------------------------------------ helpers

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function money(value) {
    var whole = Math.round(value * 100) % 100 === 0;
    return value.toLocaleString("en-US", {
      style: "currency",
      currency: "USD",
      minimumFractionDigits: whole ? 0 : 2,
      maximumFractionDigits: 2,
    });
  }

  function sizeLabel(shed) {
    return shed.width_ft + " × " + shed.length_ft;
  }

  // Matches the eagleshedsidaho.com card heading: "Lofted Barn 10 × 16".
  function titleOf(shed) {
    return shed.style + " " + sizeLabel(shed);
  }

  // lucide "arrow-right", same icon as the live site's "See Shed Details" button.
  function arrowIcon() {
    var ns = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "2");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
    svg.setAttribute("aria-hidden", "true");
    ["M5 12h14", "m12 5 7 7-7 7"].forEach(function (d) {
      var path = document.createElementNS(ns, "path");
      path.setAttribute("d", d);
      svg.appendChild(path);
    });
    return svg;
  }

  function sqft(shed) {
    return (shed.width_ft || 0) * (shed.length_ft || 0);
  }

  function findBucket(buckets, id) {
    for (var i = 0; i < buckets.length; i++) if (buckets[i].id === id) return buckets[i];
    return null;
  }

  function matches(shed, ignore) {
    if (ignore !== "style" && filters.style && shed.style !== filters.style) return false;
    if (ignore !== "size" && filters.size && !findBucket(SIZE_BUCKETS, filters.size).test(sqft(shed)))
      return false;
    if (ignore !== "price" && filters.price) {
      if (shed.cash_price_usd == null) return false;
      if (!findBucket(PRICE_BUCKETS, filters.price).test(shed.cash_price_usd)) return false;
    }
    return true;
  }

  function mailtoHref(shed) {
    var title = titleOf(shed);
    var subject = "Shed inquiry: " + title + " (ID " + shed.inventory_id + ")";
    var lines = ["Hi Eagle Sheds,", "", "I'm interested in this shed:", "", "  " + title];
    if (shed.siding_color) lines.push("  Siding: " + shed.siding_color);
    if (shed.roof_color || shed.roof_type)
      lines.push("  Roof: " + [shed.roof_color, shed.roof_type].filter(Boolean).join(" "));
    if (shed.cash_price_usd != null) lines.push("  Cash price: " + money(shed.cash_price_usd) + " + tax");
    lines.push("  Inventory ID: " + shed.inventory_id, "", "");
    return (
      "mailto:" + CONTACT_EMAIL +
      "?subject=" + encodeURIComponent(subject) +
      "&body=" + encodeURIComponent(lines.join("\n"))
    );
  }

  // Lets a parent page that listens for this message size the iframe to fit.
  // Measures the widget itself: documentElement.scrollHeight never reports less
  // than the iframe's current height, so the iframe could grow but never shrink.
  var lastHeight = 0;
  function postHeight() {
    if (window.parent === window) return;
    var height = Math.ceil(root.getBoundingClientRect().height);
    if (height === lastHeight) return;
    lastHeight = height;
    window.parent.postMessage({ type: "esb-inventory:height", height: height }, "*");
  }

  // ------------------------------------------------------------------ render

  function renderCard(shed) {
    var li = el("li", "esb-card");
    var title = titleOf(shed);

    var photo = el("div", "esb-card-photo");
    var file = shed.image_files && shed.image_files[0];
    if (file) {
      var img = document.createElement("img");
      img.src = file;
      img.loading = "lazy";
      img.decoding = "async";
      img.alt = title + (shed.siding_color ? ", " + shed.siding_color + " siding" : "");
      img.addEventListener("error", function () {
        photo.textContent = "Photo coming soon";
      });
      photo.appendChild(img);
    } else {
      photo.textContent = "Photo coming soon";
    }
    li.appendChild(photo);

    var body = el("div", "esb-card-body");
    body.appendChild(el("h3", "esb-card-title", title));

    // "Starting at $5,380 + tax", as on the live cards.
    var price = el("p", "esb-card-price");
    if (shed.cash_price_usd != null) {
      price.appendChild(el("span", "esb-price-label", "Starting at"));
      price.appendChild(el("span", "esb-price-amount", money(shed.cash_price_usd)));
      price.appendChild(el("span", "esb-price-tax", "+ tax"));
    } else {
      price.appendChild(el("span", "esb-price-amount", "Call for price"));
    }
    body.appendChild(price);

    var rto = [36, 60]
      .filter(function (months) { return shed["rto_" + months + "mo_usd"] != null; })
      .map(function (months) {
        return months + "-mo RTO " + money(shed["rto_" + months + "mo_usd"]) + "/mo";
      });
    if (rto.length) {
      // Each term is unbreakable so narrow cards wrap at the bullet, not mid-price.
      var rtoLine = el("p", "esb-card-rto");
      rto.forEach(function (text, i) {
        if (i) rtoLine.appendChild(el("span", "esb-rto-sep", "•"));
        rtoLine.appendChild(el("span", "esb-rto-term", text));
      });
      body.appendChild(rtoLine);
    }

    var specs = [];
    if (shed.siding_color) specs.push(shed.siding_color + " siding");
    if (shed.roof_color || shed.roof_type)
      specs.push([shed.roof_color, (shed.roof_type || "").toLowerCase()].filter(Boolean).join(" ") + " roof");
    // Always render (even empty) so it flexes and keeps buttons aligned across a row.
    body.appendChild(el("p", "esb-card-specs", specs.join(" • ")));

    var contact = el("a", "esb-button", "Contact us about this shed");
    contact.appendChild(arrowIcon());
    contact.href = mailtoHref(shed);
    contact.setAttribute("aria-label", "Contact us about the " + title + " (ID " + shed.inventory_id + ")");
    body.appendChild(contact);

    li.appendChild(body);
    return li;
  }

  function renderChipGroup(name, legend, options) {
    var group = el("fieldset", "esb-filter-group");
    group.appendChild(el("legend", null, legend));
    var all = [{ id: null, label: "All", count: null }].concat(options);
    all.forEach(function (opt) {
      var chip = el("button", "esb-chip", opt.label);
      chip.type = "button";
      chip.setAttribute("aria-pressed", String(filters[name] === opt.id));
      if (opt.count != null) chip.appendChild(el("span", "esb-chip-count", "(" + opt.count + ")"));
      chip.addEventListener("click", function () {
        filters[name] = opt.id;
        render();
      });
      group.appendChild(chip);
    });
    return group;
  }

  // Chip counts reflect the other active filters, and empty buckets are hidden.
  function renderFilters() {
    filtersEl.textContent = "";

    var styleCounts = {};
    var styleOrder = [];
    sheds.forEach(function (s) {
      if (!(s.style in styleCounts)) {
        styleCounts[s.style] = 0;
        styleOrder.push(s.style);
      }
      if (matches(s, "style")) styleCounts[s.style]++;
    });
    var styleOptions = styleOrder
      .filter(function (s) { return styleCounts[s] > 0 || filters.style === s; })
      .sort()
      .map(function (s) { return { id: s, label: s, count: styleCounts[s] }; });

    function bucketOptions(buckets, name, value) {
      return buckets
        .map(function (b) {
          var count = sheds.filter(function (s) {
            var v = value(s);
            return v != null && b.test(v) && matches(s, name);
          }).length;
          return { id: b.id, label: b.label, count: count };
        })
        .filter(function (o) { return o.count > 0 || filters[name] === o.id; });
    }

    filtersEl.appendChild(renderChipGroup("style", "Style", styleOptions));
    filtersEl.appendChild(renderChipGroup("size", "Size", bucketOptions(SIZE_BUCKETS, "size", sqft)));
    filtersEl.appendChild(
      renderChipGroup("price", "Price", bucketOptions(PRICE_BUCKETS, "price", function (s) {
        return s.cash_price_usd;
      }))
    );
    filtersEl.hidden = false;
  }

  function render() {
    renderFilters();
    var visible = sheds.filter(function (s) { return matches(s); });
    gridEl.textContent = "";

    if (!visible.length) {
      var empty = el("li", "esb-empty");
      empty.appendChild(el("p", null, "No sheds match those filters."));
      var reset = el("button", "esb-link-button", "Clear filters");
      reset.type = "button";
      reset.addEventListener("click", function () {
        filters = { style: null, size: null, price: null };
        render();
      });
      empty.appendChild(reset);
      gridEl.appendChild(empty);
    } else {
      visible.forEach(function (s) { gridEl.appendChild(renderCard(s)); });
    }

    statusEl.textContent =
      visible.length === sheds.length
        ? sheds.length + " sheds in stock"
        : "Showing " + visible.length + " of " + sheds.length + " sheds";
    postHeight();
  }

  function showFallback(message) {
    filtersEl.hidden = true;
    gridEl.textContent = "";
    statusEl.textContent = "";
    if (message) fallbackMsgEl.textContent = message;
    fallbackEl.classList.add("is-visible");
    postHeight();
  }

  function showUpdated(isoString) {
    var date = new Date(isoString);
    if (isNaN(date)) return;
    updatedEl.textContent =
      "Inventory updated " +
      date.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
  }

  // ------------------------------------------------------------------ load

  function load() {
    var controller = "AbortController" in window ? new AbortController() : null;
    var timer = controller && setTimeout(function () { controller.abort(); }, FETCH_TIMEOUT_MS);

    fetch(root.getAttribute("data-src"), {
      cache: "no-cache",
      signal: controller ? controller.signal : undefined,
    })
      .then(function (response) {
        if (!response.ok) throw new Error("HTTP " + response.status);
        return response.json();
      })
      .then(function (doc) {
        if (!doc || !Array.isArray(doc.sheds)) throw new Error("unexpected inventory format");
        sheds = doc.sheds.filter(function (s) { return s && s.inventory_id && s.style; });
        if (doc.generated_at) showUpdated(doc.generated_at);
        if (!sheds.length) {
          showFallback("There are no sheds in stock on our lot right now.");
          return;
        }
        render();
      })
      .catch(function (err) {
        if (window.console) console.warn("ESB inventory failed to load:", err);
        showFallback();
      })
      .then(function () {
        if (timer) clearTimeout(timer);
      });
  }

  // A parent that loads before the iframe can ask for the current height.
  window.addEventListener("message", function (event) {
    if (event.data && event.data.type === "esb-inventory:request-height") {
      lastHeight = 0;
      postHeight();
    }
  });
  if ("ResizeObserver" in window) new ResizeObserver(postHeight).observe(root);
  window.addEventListener("load", postHeight);
  load();
})();
