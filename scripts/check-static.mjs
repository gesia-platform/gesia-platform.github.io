#!/usr/bin/env node

import { access, readFile, readdir } from 'node:fs/promises';
import path from 'node:path';
import process from 'node:process';

const root = path.resolve(import.meta.dirname, '..');

async function exists(filePath) {
  try {
    await access(filePath);
    return true;
  } catch {
    return false;
  }
}

function localReference(value, baseDir) {
  const cleaned = value.trim().replace(/^['"]|['"]$/g, '').split(/[?#]/)[0];
  if (!cleaned || /^(?:https?:|data:|mailto:|tel:|javascript:|#|\/\/)/i.test(cleaned)) return null;
  return path.resolve(baseDir, cleaned);
}

async function main() {
  const references = [];
  const htmlPath = path.join(root, 'index.html');
  const html = await readFile(htmlPath, 'utf8');
  for (const match of html.matchAll(/(?:src|href)=["']([^"']+)["']/gi)) {
    const reference = localReference(match[1], root);
    if (reference) references.push(reference);
  }

  const cssDir = path.join(root, 'common', 'css');
  for (const name of await readdir(cssDir)) {
    if (!name.endsWith('.css')) continue;
    const cssPath = path.join(cssDir, name);
    const css = await readFile(cssPath, 'utf8');
    for (const match of css.matchAll(/url\(([^)]+)\)/gi)) {
      const reference = localReference(match[1], cssDir);
      if (reference) references.push(reference);
    }
  }

  const unique = [...new Set(references)];
  const missing = [];
  for (const reference of unique) {
    if (!(await exists(reference))) missing.push(path.relative(root, reference));
  }

  process.stdout.write(`Checked ${unique.length} local references.\n`);
  if (missing.length) {
    process.stderr.write(`Missing ${missing.length}:\n${missing.join('\n')}\n`);
    process.exitCode = 1;
    return;
  }
  process.stdout.write('All referenced local files are present.\n');
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error.message}\n`);
  process.exitCode = 1;
});
