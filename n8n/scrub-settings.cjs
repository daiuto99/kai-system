'use strict';

const fs = require('node:fs');
const path = require('node:path');

const userFolder = process.env.N8N_USER_FOLDER || path.join(process.env.HOME, '.n8n');
const candidates = ['config', '.syncthing.config.tmp'];

for (const name of candidates) {
    const target = path.join(userFolder, name);
    if (!fs.existsSync(target)) continue;
    const settings = JSON.parse(fs.readFileSync(target, 'utf8'));
    if (!Object.hasOwn(settings, 'encryptionKey')) continue;
    delete settings.encryptionKey;
    if (Object.keys(settings).length === 0) {
        fs.unlinkSync(target);
    } else {
        fs.writeFileSync(target, JSON.stringify(settings, null, '\t'), { mode: 0o600 });
    }
}
