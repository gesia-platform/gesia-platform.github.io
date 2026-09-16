#!/usr/bin/env node

import { access, readFile, readdir, stat } from 'node:fs/promises';
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
  if (cleaned.startsWith('/')) return path.join(root, cleaned.replace(/^\/+/, ''));
  return path.resolve(baseDir, cleaned);
}

async function walk(directory) {
  const files = [];
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    if (entry.name === '.git') continue;
    const absolute = path.join(directory, entry.name);
    if (entry.isDirectory()) files.push(...await walk(absolute));
    else files.push(absolute);
  }
  return files;
}

async function referenceExists(reference) {
  if (!(await exists(reference))) return false;
  const details = await stat(reference);
  if (!details.isDirectory()) return true;
  return exists(path.join(reference, 'index.html'));
}

async function main() {
  const references = [];
  const files = await walk(root);
  for (const htmlPath of files.filter((file) => file.endsWith('.html'))) {
    const html = await readFile(htmlPath, 'utf8');
    for (const match of html.matchAll(/(?:src|href)=["']([^"']+)["']/gi)) {
      const reference = localReference(match[1], path.dirname(htmlPath));
      if (reference) references.push(reference);
    }
  }

  for (const cssPath of files.filter((file) => file.endsWith('.css'))) {
    const css = await readFile(cssPath, 'utf8');
    for (const match of css.matchAll(/url\(([^)]+)\)/gi)) {
      const reference = localReference(match[1], path.dirname(cssPath));
      if (reference) references.push(reference);
    }
  }

  const unique = [...new Set(references)];
  const missing = [];
  for (const reference of unique) {
    if (!(await referenceExists(reference))) missing.push(path.relative(root, reference));
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
