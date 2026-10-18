import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { cpSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';

const sourceProject = fileURLToPath(new URL('..', import.meta.url));
function fixture() {
    const root = mkdtempSync(join(tmpdir(), 'chordsense-web-build-test-'));
    const project = join(root, 'web');
    for (const directory of ['scripts', 'src', 'server']) cpSync(join(sourceProject, directory), join(project, directory), { recursive: true });
    cpSync(join(sourceProject, 'package.json'), join(project, 'package.json'));
    return {
        root, project, output: join(project, 'dist'), deployment: join(root, '.vercel', 'output'),
        build(endpoint = '') {
            return execFileSync(process.execPath, [join(project, 'scripts', 'build.mjs')], {
                env: { ...process.env, CHORDSENSE_API_URL: endpoint, CHORDSENSE_DISCOVERY_TOKEN: 'ENV_SECRET_MUST_NOT_PUBLISH' }, stdio: 'pipe',
            });
        },
        cleanup() { rmSync(root, { recursive: true, force: true }); },
    };
}
function snapshot(directory, prefix = '') {
    const files = {};
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
        const relative = prefix + entry.name;
        if (entry.isDirectory()) Object.assign(files, snapshot(join(directory, entry.name), relative + '/'));
        else files[relative] = readFileSync(join(directory, entry.name), 'base64');
    }
    return files;
}
function selectedRoutes(config, pathname) {
    const selected = [];
    for (const route of config.routes) {
        if (!route.src || !new RegExp(route.src).test(pathname)) continue;
        selected.push(route);
        if (route.dest && !route.continue) break;
    }
    return selected;
}

test('build emits a real Node discovery function and never embeds a Wi-Fi address or secret', () => {
    const f = fixture();
    try {
        f.build();
        const configText = readFileSync(join(f.output, 'config.js'), 'utf8');
        assert.match(configText, /discoveryPath/);
        assert.ok(!configText.includes('defaultApiUrl'));
        const fn = join(f.deployment, 'functions', 'api', 'discovery.func');
        const metadata = JSON.parse(readFileSync(join(fn, '.vc-config.json'), 'utf8'));
        assert.equal(metadata.runtime, 'nodejs22.x');
        assert.equal(metadata.handler, 'index.js');
        assert.equal(metadata.maxDuration, 10);
        assert.match(readFileSync(join(fn, 'index.js'), 'utf8'), /createHandler/);
        assert.match(readFileSync(join(fn, 'discovery-store.cjs'), 'utf8'), /createStore/);
        assert.equal(readFileSync(join(fn, 'index.html'), 'utf8'), readFileSync(join(f.output, 'index.html'), 'utf8'));
        assert.deepEqual(snapshot(join(f.deployment, 'static')), snapshot(f.output));
        for (const value of Object.values(snapshot(f.deployment))) {
            const text = Buffer.from(value, 'base64').toString();
            assert.ok(!text.includes('ENV_SECRET_MUST_NOT_PUBLISH'));
            assert.ok(!text.includes('10.136.137.225'));
            assert.ok(!text.includes('100.71.233.14'));
        }
    } finally { f.cleanup(); }
});

test('dynamic page and directory routes do not inherit a conflicting static connect-src header', () => {
    const f = fixture();
    try {
        f.build();
        const config = JSON.parse(readFileSync(join(f.deployment, 'config.json'), 'utf8'));
        assert.equal(config.version, 3);
        for (const routePath of ['/', '/index.html', '/upload', '/upload/']) {
            const routes = selectedRoutes(config, routePath);
            assert.equal(routes.at(-1).dest, '/api/discovery?action=page');
            assert.ok(!routes.some(route => route.headers?.['Content-Security-Policy']));
        }
        for (const action of ['publish', 'resolve']) {
            assert.equal(selectedRoutes(config, `/api/discovery/${action}`).at(-1).dest, '/api/discovery?action=$1');
        }
        const staticHeaders = Object.assign({}, ...selectedRoutes(config, '/config.js').map(route => route.headers || {}));
        assert.match(staticHeaders['Content-Security-Policy'], /connect-src 'self';/);
        assert.equal(staticHeaders['Cache-Control'], 'no-store');
        assert.deepEqual(config.routes.at(-1), { handle: 'filesystem' });
        const projectConfig = JSON.parse(readFileSync(join(sourceProject, '..', 'vercel.json'), 'utf8'));
        for (const duplicated of ['outputDirectory', 'headers', 'rewrites', 'routes']) assert.equal(projectConfig[duplicated], undefined);
    } finally { f.cleanup(); }
});

test('obsolete build-time endpoint overrides cannot overwrite the last discovery build', () => {
    const f = fixture();
    try {
        f.build();
        const previous = snapshot(f.deployment);
        for (const endpoint of ['http://10.1.2.3:5051', 'http://chordsense.local:5051', 'http://100.71.233.14:5051']) {
            assert.throws(() => f.build(endpoint), /Automatic discovery does not use/);
            assert.deepEqual(snapshot(f.deployment), previous);
        }
    } finally { f.cleanup(); }
});

test('public assets exclude server modules, tests, secret files, native code, and symlinks', () => {
    const f = fixture();
    try {
        const source = join(f.project, 'src');
        const sentinel = 'FILE_SECRET_MUST_NOT_PUBLISH';
        for (const name of ['.env', 'private.pem', 'credentials.json', 'native.rs', 'helper.spec.js', 'helper.test.js']) writeFileSync(join(source, name), sentinel);
        for (const directory of ['tests', 'fixtures', 'src-tauri']) {
            mkdirSync(join(source, directory));
            writeFileSync(join(source, directory, 'private.js'), sentinel);
        }
        symlinkSync(join(source, '.env'), join(source, 'linked-secret.js'));
        f.build();
        const publicFiles = snapshot(join(f.deployment, 'static'));
        assert.ok(publicFiles['index.html']);
        assert.ok(publicFiles['endpoint.js']);
        for (const [name, value] of Object.entries(publicFiles)) {
            assert.ok(!/\.(?:test|spec)\.|\.cjs$|(?:^|\/)(?:tests|fixtures|src-tauri)\//.test(name));
            assert.ok(!Buffer.from(value, 'base64').toString().includes(sentinel));
            assert.ok(!Buffer.from(value, 'base64').toString().includes('UPSTASH_REDIS_REST_TOKEN'));
        }
    } finally { f.cleanup(); }
});
