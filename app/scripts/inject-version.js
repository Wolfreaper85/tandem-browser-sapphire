#!/usr/bin/env node
/**
 * Inject version from package.json into shell/about.html
 * Run during npm compile
 */
const fs = require('fs');
const path = require('path');

const pkgPath = path.join(__dirname, '..', 'package.json');
const aboutPath = path.join(__dirname, '..', 'shell', 'about.html');

const pkg = JSON.parse(fs.readFileSync(pkgPath, 'utf-8'));
let html = fs.readFileSync(aboutPath, 'utf-8');

// Replace __VERSION__ placeholder with actual version
html = html.replace(/__VERSION__/g, pkg.version);

fs.writeFileSync(aboutPath, html);
console.log(`✅ Injected version ${pkg.version} into about.html`);
