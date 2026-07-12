'use strict';

const fs = require('node:fs');
const path = require('node:path');

const root = '/usr/local/lib/node_modules/n8n';
const suffix = path.join('n8n-core', 'dist', 'instance-settings', 'instance-settings.js');

function findTargets(directory, targets = []) {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
        const candidate = path.join(directory, entry.name);
        if (entry.isDirectory()) {
            findTargets(candidate, targets);
        } else if (candidate.endsWith(suffix)) {
            targets.push(candidate);
        }
    }
    return targets;
}

function replaceExactlyOnce(source, before, after, label) {
    const first = source.indexOf(before);
    if (first < 0 || source.indexOf(before, first + before.length) >= 0) {
        throw new Error(`n8n secretless-config patch failed at ${label}`);
    }
    return source.replace(before, after);
}

const targets = findTargets(root);
if (targets.length === 0) {
    throw new Error('n8n InstanceSettings implementation not found');
}

for (const target of targets) {
    let source = fs.readFileSync(target, 'utf8');
    source = replaceExactlyOnce(
        source,
        `            const { encryptionKey, tunnelSubdomain, fsStorageMigrated } = settings;\n            if (encryptionKeyFromEnv && encryptionKey !== encryptionKeyFromEnv) {\n                throw new n8n_workflow_1.ApplicationError(\`Mismatching encryption keys. The encryption key in the settings file \${this.settingsFile} does not match the N8N_ENCRYPTION_KEY env var. Please make sure both keys match. More information: https://docs.n8n.io/hosting/environment-variables/configuration-methods/#encryption-key\`);\n            }\n            return { encryptionKey, tunnelSubdomain, fsStorageMigrated };`,
        `            const { encryptionKey: encryptionKeyFromFile, tunnelSubdomain, fsStorageMigrated } = settings;\n            if (encryptionKeyFromEnv && encryptionKeyFromFile && encryptionKeyFromFile !== encryptionKeyFromEnv) {\n                throw new n8n_workflow_1.ApplicationError(\`Mismatching encryption keys. The encryption key in the settings file \${this.settingsFile} does not match the N8N_ENCRYPTION_KEY env var. Please make sure both keys match. More information: https://docs.n8n.io/hosting/environment-variables/configuration-methods/#encryption-key\`);\n            }\n            const encryptionKey = encryptionKeyFromEnv ?? encryptionKeyFromFile;\n            return { encryptionKey, tunnelSubdomain, fsStorageMigrated };`,
        'load-existing',
    );
    source = replaceExactlyOnce(
        source,
        `        this.save(settings);\n        this.ensureSettingsFilePermissions();\n        return settings;`,
        `        this.save(settings);\n        if ((0, node_fs_1.existsSync)(this.settingsFile))\n            this.ensureSettingsFilePermissions();\n        return settings;`,
        'load-or-create',
    );
    source = replaceExactlyOnce(
        source,
        `    save(settings) {\n        this.settings = settings;\n        (0, node_fs_1.writeFileSync)(this.settingsFile, JSON.stringify(this.settings, null, '\\t'), {\n            mode: this.enforceSettingsFilePermissions.enforce ? 0o600 : undefined,\n            encoding: 'utf-8',\n        });\n    }`,
        `    save(settings) {\n        this.settings = settings;\n        const persistedSettings = { ...settings };\n        if (this.config.encryptionKey)\n            delete persistedSettings.encryptionKey;\n        if (Object.keys(persistedSettings).length === 0) {\n            try {\n                (0, node_fs_1.unlinkSync)(this.settingsFile);\n            }\n            catch (error) {\n                if (error.code !== 'ENOENT')\n                    throw error;\n            }\n            return;\n        }\n        (0, node_fs_1.writeFileSync)(this.settingsFile, JSON.stringify(persistedSettings, null, '\\t'), {\n            mode: this.enforceSettingsFilePermissions.enforce ? 0o600 : undefined,\n            encoding: 'utf-8',\n        });\n    }`,
        'save',
    );
    fs.writeFileSync(target, source);
}
