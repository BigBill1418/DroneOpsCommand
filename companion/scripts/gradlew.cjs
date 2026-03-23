#!/usr/bin/env node
/**
 * Cross-platform Gradle wrapper runner.
 * Usage: node scripts/gradlew.js assembleDebug
 */
const { execSync } = require('child_process');
const path = require('path');
const os = require('os');

const args = process.argv.slice(2).join(' ');
const androidDir = path.join(__dirname, '..', 'android');
const isWindows = os.platform() === 'win32';
const cmd = isWindows ? `gradlew.bat ${args}` : `./gradlew ${args}`;

console.log(`Running: ${cmd} (in ${androidDir})\n`);

try {
  execSync(cmd, { cwd: androidDir, stdio: 'inherit' });
} catch (e) {
  process.exit(e.status || 1);
}
