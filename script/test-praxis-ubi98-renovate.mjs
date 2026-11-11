// Run against the installed Renovate package, without network access:
// RENOVATE_POLICY_JSON="$(< renovate/praxis-ai-grid-renovate.json5)" \
// podman run --rm -i --pull=never --network=none -e RENOVATE_POLICY_JSON \
//   --entrypoint node localhost/mintmaker-renovate:local --input-type=module \
//   < script/test-praxis-ubi98-renovate.mjs
import assert from 'node:assert/strict';
const root = process.env.RENOVATE_PACKAGE_ROOT || '/home/renovate/renovate';
const { regex_exports: regex } = await import(`${root}/dist/modules/manager/custom/regex/index.js`);
const { doAutoReplace } = await import(`${root}/dist/workers/repository/update/branch/auto-replace.js`);
const { GlobalConfig } = await import(`${root}/dist/config/global.js`);
const { validateConfig } = await import(`${root}/dist/config/validation.js`);
const policy = JSON.parse(process.env.RENOVATE_POLICY_JSON);
const manager = policy.customManagers[0];
GlobalConfig.set({ localDir: '/tmp/praxis-policy-tests' });
const name = 'registry.redhat.io/ubi9/ubi-minimal-pqc';
const oldDigest = `sha256:${'a'.repeat(64)}`;
const newDigest = `sha256:${'b'.repeat(64)}`;
// Validate this policy's additions separately from inherited production config.
const { extends: inherited, enabledManagers, ...additions } = policy;
assert(inherited.length);
const validation = await validateConfig('repo', {
  ...additions,
  enabledManagers: ['dockerfile', 'rpm-lockfile', 'custom.regex'],
});
assert.deepEqual(validation.errors, []);
assert.deepEqual(validation.warnings, []);
for (const content of [
  `FROM ${name}@${oldDigest}\n`,
  `FROM ${name}:9.8@${oldDigest} AS runtime\n`,
  `# build\nFROM --platform=linux/amd64 ${name}@${oldDigest} AS runtime\n`,
  `  from ${name}@${oldDigest}\r\n`,
]) {
  const extracted = regex.extractPackageFile(content, 'Dockerfile.konflux', manager);
  assert.equal(extracted.deps.length, 1);
  assert.equal(extracted.deps[0].currentValue, '9.8');
  const rendered = await doAutoReplace({
    ...extracted.deps[0], ...extracted, deps: undefined,
    manager: 'regex', packageFile: 'Dockerfile.konflux', depIndex: 0,
    newValue: '9.8', newDigest, updateType: 'digest',
  }, content, false);
  assert.equal(rendered, content.replace(`${name}${content.includes(`${name}:9.8`) ? ':9.8' : ''}@${oldDigest}`, `${name}:9.8@${newDigest}`));
  const updated = regex.extractPackageFile(rendered, 'Dockerfile.konflux', manager);
  assert.equal(updated.deps[0].currentDigest, newDigest);
}
for (const content of [
  `# FROM ${name}@${oldDigest}\n`,
  `RUN echo ${name}@${oldDigest}\n`,
  `ARG BASE=${name}@${oldDigest}\n`,
  `FROM ${name}:9.9@${oldDigest}\n`,
  `FROM registry.redhat.io/ubi9/ubi-minimal@${oldDigest}\n`,
]) {
  assert.equal(regex.extractPackageFile(content, 'Dockerfile.konflux', manager)?.deps?.length || 0, 0);
}
const adjacent = `FROM ${name}@${oldDigest} AS first\nFROM ${name}@${oldDigest} AS second\n`;
assert.equal(regex.extractPackageFile(adjacent, 'Dockerfile.konflux', manager).deps.length, 2);
console.log('PASS: policy additions validate; tagged/untagged FROM, platform, aliases, CRLF, adjacent stages and excluded references checked.');
