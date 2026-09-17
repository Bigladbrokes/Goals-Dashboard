#!/usr/bin/env node
/**
 * Validates every file in data/ against the shape index.html renders.
 * Run: node scripts/validate-data.mjs
 * Exits 1 and prints every problem found, so a malformed file fails the build
 * before it is deployed.
 */
import { readFile, readdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, join, resolve } from "node:path";

const DATA_DIR = resolve(dirname(fileURLToPath(import.meta.url)), "..", "data");
const CATEGORIES = ["course", "book", "project", "other"];
const STEP_COUNT = 10;

const errors = [];
const fail = (file, msg) => errors.push(`${file}: ${msg}`);

const isObject = (v) => v !== null && typeof v === "object" && !Array.isArray(v);
const isYear = (v) => Number.isInteger(v) && v >= 1970 && v <= 9999;
const isFilledString = (v) => typeof v === "string" && v.trim() !== "";

function isDate(v) {
  if (typeof v !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(v)) return false;
  const [y, m, d] = v.split("-").map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d));
  return dt.getUTCFullYear() === y && dt.getUTCMonth() === m - 1 && dt.getUTCDate() === d;
}

async function readJSON(file) {
  let text;
  try {
    text = await readFile(join(DATA_DIR, file), "utf8");
  } catch (e) {
    fail(file, `cannot read file (${e.code || e.message})`);
    return null;
  }
  try {
    return JSON.parse(text);
  } catch (e) {
    fail(file, `invalid JSON — ${e.message}`);
    return null;
  }
}

function checkIndex(data) {
  const file = "index.json";
  if (!isObject(data)) {
    fail(file, "must be a JSON object");
    return [];
  }
  if (!Array.isArray(data.years) || data.years.length === 0) {
    fail(file, 'key "years" must be a non-empty array of integer years, e.g. [2026]');
    return [];
  }
  const bad = data.years.filter((y) => !isYear(y));
  if (bad.length) fail(file, `key "years" has non-year entries: ${bad.map((b) => JSON.stringify(b)).join(", ")}`);
  const years = data.years.filter(isYear);
  const dupes = years.filter((y, i) => years.indexOf(y) !== i);
  if (dupes.length) fail(file, `key "years" has duplicates: ${[...new Set(dupes)].join(", ")}`);
  if (!isYear(data.default)) fail(file, 'key "default" must be an integer year');
  else if (!years.includes(data.default)) fail(file, `key "default" (${data.default}) is not listed in "years"`);
  return [...new Set(years)];
}

function checkYearFile(file, data, year) {
  if (!isObject(data)) {
    fail(file, "must be a JSON object");
    return;
  }
  if (data.year !== year) fail(file, `key "year" is ${JSON.stringify(data.year)} but the filename says ${year}`);
  if (data.updatedAt != null && !isDate(data.updatedAt)) fail(file, 'key "updatedAt" must be a YYYY-MM-DD date or absent');
  if (!Array.isArray(data.projects)) {
    fail(file, 'key "projects" must be an array');
    return;
  }

  const seen = new Set();
  data.projects.forEach((p, i) => {
    const at = `projects[${i}]`;
    if (!isObject(p)) {
      fail(file, `${at} must be an object`);
      return;
    }
    if (!isFilledString(p.id)) fail(file, `${at}.id must be a non-empty string`);
    else if (seen.has(p.id)) fail(file, `${at}.id "${p.id}" is duplicated`);
    else seen.add(p.id);

    if (!isFilledString(p.title)) fail(file, `${at}.title must be a non-empty string`);
    if (!CATEGORIES.includes(p.category)) fail(file, `${at}.category must be one of ${CATEGORIES.join(", ")} (got ${JSON.stringify(p.category)})`);
    if (p.syncKey != null && !isFilledString(p.syncKey)) fail(file, `${at}.syncKey must be null or a non-empty string`);
    if (p.syncedAt != null && !isDate(p.syncedAt)) fail(file, `${at}.syncedAt must be null or a YYYY-MM-DD date`);
    for (const key of ["subtitle", "source", "note"]) {
      if (p[key] != null && typeof p[key] !== "string") fail(file, `${at}.${key} must be a string`);
    }

    if (!Array.isArray(p.steps) || p.steps.length !== STEP_COUNT) {
      fail(file, `${at}.steps must be an array of exactly ${STEP_COUNT} entries (got ${Array.isArray(p.steps) ? p.steps.length : typeof p.steps})`);
      return;
    }
    p.steps.forEach((s, j) => {
      const sat = `${at}.steps[${j}]`;
      if (!isObject(s)) {
        fail(file, `${sat} must be an object`);
        return;
      }
      if (typeof s.done !== "boolean") fail(file, `${sat}.done must be true or false`);
      if (s.date != null && !isDate(s.date)) fail(file, `${sat}.date must be null or a YYYY-MM-DD date (got ${JSON.stringify(s.date)})`);
      if (s.done === false && s.date != null) fail(file, `${sat} is not done but carries a date`);
    });
  });
}

const index = await readJSON("index.json");
const listedYears = index === null ? [] : checkIndex(index);

const files = (await readdir(DATA_DIR)).filter((f) => f.endsWith(".json")).sort();
const yearFiles = new Map();
for (const file of files) {
  if (file === "index.json") continue;
  const m = /^(\d{4})\.json$/.exec(file);
  if (!m) {
    fail(file, "unexpected file in data/ — year files must be named <year>.json");
    continue;
  }
  yearFiles.set(Number(m[1]), file);
}

for (const [year, file] of [...yearFiles].sort((a, b) => a[0] - b[0])) {
  if (!listedYears.includes(year)) fail(file, `year ${year} is not listed in index.json "years"`);
  const data = await readJSON(file);
  if (data !== null) checkYearFile(file, data, year);
}

for (const year of listedYears) {
  if (!yearFiles.has(year)) fail("index.json", `year ${year} is listed but data/${year}.json is missing`);
}

const checked = [...yearFiles.values(), "index.json"].length;
if (errors.length) {
  console.error(`data/ validation failed with ${errors.length} problem(s):`);
  for (const e of errors) console.error(`  - ${e}`);
  process.exit(1);
}
console.log(`data/ ok — ${checked} file(s) checked, years: ${listedYears.join(", ") || "none"}`);
