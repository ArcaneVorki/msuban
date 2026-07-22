// Daily wallet ban-check fetcher
// Reads data/addresses.json, batches into groups of 50, hits the API,
// and writes results into data/latest.json + data/history/<date>.json

import fs from "fs";
import path from "path";

const API_URL = "https://api.msu123.com/api/wallet-analysis/ban-check";
const BATCH_SIZE = 50;
const DELAY_BETWEEN_BATCHES_MS = 60000; // be polite to their API
const MAX_RETRIES = 3;

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function chunk(arr, size) {
  const out = [];
  for (let i = 0; i < arr.length; i += size) {
    out.push(arr.slice(i, i + size));
  }
  return out;
}

async function fetchBatch(addresses, attempt = 1) {
  try {
    const res = await fetch(API_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ addresses }),
    });

    if (!res.ok) {
      throw new Error(`HTTP ${res.status}: ${res.statusText}`);
    }

    const data = await res.json();
    return data.results;
  } catch (err) {
    if (attempt < MAX_RETRIES) {
      console.error(
        `Batch failed (attempt ${attempt}/${MAX_RETRIES}): ${err.message}. Retrying...`
      );
      await sleep(1000 * attempt);
      return fetchBatch(addresses, attempt + 1);
    }
    console.error(
      `Batch failed permanently after ${MAX_RETRIES} attempts: ${err.message}`
    );
    return addresses.map((address) => ({
      address,
      banInfo: null,
      error: err.message,
    }));
  }
}

// Maintains data/ban-history.json — a persistent per-address record of every
// distinct ban period ever observed, so we can tell repeat offenders apart
// from addresses banned once and never again.
function updateBanHistory(results, checkedAt) {
  const historyPath = path.join("data", "ban-history.json");
  let history = {};
  if (fs.existsSync(historyPath)) {
    history = JSON.parse(fs.readFileSync(historyPath, "utf-8"));
  }

  for (const r of results) {
    const addr = r.address;
    const isBanned = !!r.banInfo?.banned;

    if (!history[addr]) {
      history[addr] = {
        timesBanned: 0,
        currentlyBanned: false,
        periods: [],
        lastCheckedAt: checkedAt,
      };
    }
    const entry = history[addr];
    entry.lastCheckedAt = checkedAt;

    if (isBanned) {
      const lastPeriod = entry.periods[entry.periods.length - 1];
      // A "new" ban period is one we haven't already recorded — either there's
      // no prior period, the prior one was closed out (address was clean in
      // between), or the API's own banStartAt doesn't match what we have.
      const isNewPeriod =
        !lastPeriod ||
        lastPeriod.closed === true ||
        lastPeriod.banStartAt !== (r.banInfo.banStartAt || null);

      if (isNewPeriod) {
        entry.periods.push({
          banStartAt: r.banInfo.banStartAt || null,
          banEndAt: r.banInfo.banEndAt || null,
          isPermanentBan: !!r.banInfo.isPermanentBan,
          closed: false,
        });
        entry.timesBanned += 1;
      } else {
        // Same ongoing ban — just refresh in case end date or permanence changed
        lastPeriod.banEndAt = r.banInfo.banEndAt || lastPeriod.banEndAt;
        lastPeriod.isPermanentBan = !!r.banInfo.isPermanentBan;
      }
      entry.currentlyBanned = true;
    } else {
      const lastPeriod = entry.periods[entry.periods.length - 1];
      if (lastPeriod && !lastPeriod.closed) {
        lastPeriod.closed = true;
      }
      entry.currentlyBanned = false;
    }
  }

  fs.writeFileSync(historyPath, JSON.stringify(history, null, 2));
  return history;
}

async function main() {
  const addressesPath = path.join("data", "addresses.json");
  const addresses = JSON.parse(fs.readFileSync(addressesPath, "utf-8"));

  if (!Array.isArray(addresses) || addresses.length === 0) {
    console.error("No addresses found in data/addresses.json — aborting.");
    process.exit(1);
  }

  const batches = chunk(addresses, BATCH_SIZE);
  console.log(
    `Checking ${addresses.length} addresses across ${batches.length} batch(es)...`
  );

  let allResults = [];
  for (let i = 0; i < batches.length; i++) {
    const results = await fetchBatch(batches[i]);
    allResults = allResults.concat(results);
    console.log(`  batch ${i + 1}/${batches.length} done`);
    if (i < batches.length - 1) {
      await sleep(DELAY_BETWEEN_BATCHES_MS);
    }
  }

  const today = new Date().toISOString().slice(0, 10); // YYYY-MM-DD
  const record = {
    date: today,
    checkedAt: new Date().toISOString(),
    totalAddresses: addresses.length,
    results: allResults,
  };

  const historyDir = path.join("data", "history");
  fs.mkdirSync(historyDir, { recursive: true });
  fs.writeFileSync(
    path.join(historyDir, `${today}.json`),
    JSON.stringify(record, null, 2)
  );
  fs.writeFileSync(
    path.join("data", "latest.json"),
    JSON.stringify(record, null, 2)
  );

  // Update manifest so the dashboard knows which history files exist
  const manifestPath = path.join("data", "manifest.json");
  let manifest = [];
  if (fs.existsSync(manifestPath)) {
    manifest = JSON.parse(fs.readFileSync(manifestPath, "utf-8"));
  }
  if (!manifest.includes(today)) {
    manifest.push(today);
    manifest.sort();
  }
  fs.writeFileSync(manifestPath, JSON.stringify(manifest, null, 2));

  const banHistory = updateBanHistory(allResults, record.checkedAt);
  const repeatOffenders = Object.values(banHistory).filter(
    (e) => e.timesBanned > 1
  ).length;

  const bannedCount = allResults.filter((r) => r.banInfo?.banned).length;
  console.log(
    `Done. ${bannedCount}/${allResults.length} addresses currently banned. ` +
    `${repeatOffenders} repeat offender(s) in ban history.`
  );
}

main();
