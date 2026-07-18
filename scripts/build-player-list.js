// Builds the top-N player + wallet list.
//
// Step 1: paginate the ranking API to collect characterAssetKeys for the top N players.
// Step 2: for each character, call the character-info API to get walletAddr + attackPower.
// Step 3: write data/players.json (full detail) and data/addresses.json (deduped wallets,
//         consumed by scripts/fetch.js for the daily ban check).
//
// Configurable via env vars (all optional):
//   TOTAL_PLAYERS - how many top players to pull (default 5000)
//   PAGE_SIZE     - ranking page size (default 10 — matches the tested endpoint;
//                   bump this if you confirm the API accepts a larger page size,
//                   it'll cut the number of ranking requests way down)
//   CONCURRENCY   - parallel in-flight requests (default 1, so DELAY_MS is a true
//                   gap between every single API call — raise this if you want
//                   parallel lanes instead, but then DELAY_MS only paces each lane)
//   DELAY_MS      - delay per request slot, ms (default 1000 = 1 second)

import fs from "fs";
import path from "path";

const RANKING_API = "https://msu.io/maplestoryn/api/msn/ranking";
const CHARACTER_API = "https://msu.io/navigator/api/navigator/characters";

const TOTAL_PLAYERS = parseInt(process.env.TOTAL_PLAYERS || "5000", 10);
const PAGE_SIZE = parseInt(process.env.PAGE_SIZE || "10", 10);
const TOTAL_PAGES = Math.ceil(TOTAL_PLAYERS / PAGE_SIZE);
const CONCURRENCY = parseInt(process.env.CONCURRENCY || "1", 10);
const DELAY_MS = parseInt(process.env.DELAY_MS || "1000", 10);
const MAX_RETRIES = 3;

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function fetchJSON(url, attempt = 1) {
  try {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (err) {
    if (attempt < MAX_RETRIES) {
      await sleep(500 * attempt);
      return fetchJSON(url, attempt + 1);
    }
    throw err;
  }
}

// Runs `worker` over `items` with at most `concurrency` in flight at once,
// waiting DELAY_MS between each request a given worker slot makes.
async function runWithConcurrency(items, worker, concurrency) {
  let idx = 0;
  async function lane() {
    while (idx < items.length) {
      const current = idx++;
      await worker(items[current], current);
      await sleep(DELAY_MS);
    }
  }
  await Promise.all(Array.from({ length: concurrency }, lane));
}

async function fetchRankingPages() {
  console.log(`Fetching ${TOTAL_PAGES} ranking page(s) (pageSize=${PAGE_SIZE})...`);
  const pages = Array.from({ length: TOTAL_PAGES }, (_, i) => i + 1);
  const allEntries = [];

  await runWithConcurrency(pages, async (pageNo) => {
    const url =
      `${RANKING_API}?rankingFilter.classCode=-1&rankingFilter.jobCode=-1` +
      `&paginationParam.pageNo=${pageNo}&paginationParam.pageSize=${PAGE_SIZE}`;
    try {
      const data = await fetchJSON(url);
      allEntries.push(...(data.ranking || []));
      if (pageNo % 25 === 0 || pageNo === TOTAL_PAGES) {
        console.log(`  ranking page ${pageNo}/${TOTAL_PAGES} ok`);
      }
    } catch (err) {
      console.error(`  ranking page ${pageNo} failed permanently: ${err.message}`);
    }
  }, CONCURRENCY);

  allEntries.sort((a, b) => a.rank - b.rank);
  return allEntries.slice(0, TOTAL_PLAYERS);
}

async function fetchCharacterDetails(entries) {
  console.log(`Fetching wallet + attack power for ${entries.length} characters...`);
  const players = [];
  let done = 0;

  await runWithConcurrency(entries, async (entry) => {
    const url = `${CHARACTER_API}/${entry.characterAssetKey}/info`;
    try {
      const data = await fetchJSON(url);
      const c = data.character;
      players.push({
        rank: entry.rank,
        characterAssetKey: entry.characterAssetKey,
        characterName: entry.characterName,
        level: entry.level,
        classCode: entry.classCode,
        jobCode: entry.jobCode,
        guildName: entry.guildName,
        imageUrl: c?.imageUrl || entry.imageUrl || null,
        walletAddr: c?.owner?.walletAddr || null,
        attackPower: c?.apStat?.attackPower || null,
      });
    } catch (err) {
      console.error(`  character ${entry.characterAssetKey} failed permanently: ${err.message}`);
      players.push({
        rank: entry.rank,
        characterAssetKey: entry.characterAssetKey,
        characterName: entry.characterName,
        level: entry.level,
        imageUrl: entry.imageUrl || null,
        walletAddr: null,
        attackPower: null,
        error: err.message,
      });
    } finally {
      done++;
      if (done % 100 === 0 || done === entries.length) {
        console.log(`  ...${done}/${entries.length} characters processed`);
      }
    }
  }, CONCURRENCY);

  players.sort((a, b) => a.rank - b.rank);
  return players;
}

async function main() {
  const rankingEntries = await fetchRankingPages();
  console.log(`Got ${rankingEntries.length} ranking entries.`);

  if (rankingEntries.length === 0) {
    console.error("No ranking entries retrieved — aborting before overwriting existing data.");
    process.exit(1);
  }

  const players = await fetchCharacterDetails(rankingEntries);

  fs.mkdirSync("data", { recursive: true });
  fs.writeFileSync("data/players.json", JSON.stringify(players, null, 2));

  const addresses = [...new Set(players.map((p) => p.walletAddr).filter(Boolean))];
  fs.writeFileSync("data/addresses.json", JSON.stringify(addresses, null, 2));

  const missingWallets = players.filter((p) => !p.walletAddr).length;
  console.log(
    `Done. ${players.length} players saved to data/players.json, ` +
    `${addresses.length} unique wallet addresses saved to data/addresses.json` +
    (missingWallets ? ` (${missingWallets} players had no wallet resolved).` : ".")
  );
}

main();
