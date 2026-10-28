// baritone-probe: the behavior pack baritone-bedrock reads the world through. It is added to a test world beside the
// game's own packs and talks to the runner on this machine (baritone_bedrock/probe.py). It gives a Baritone-style
// navigator what the Script API can tell it and nothing else: nothing here moves the player or changes the world,
// except the commands the runner itself sends back in a /tick answer.
//
//   POST /tick   {tick, players: [...], events: [...], blocks: [...], results: [...]}
//                player: {id, name, dim, x, y, z, yaw, pitch, vx, vy, vz, ground, water, sneak, sprint, jump, climbing,
//                         swimming, falling, fall, slot, hand, hotbar, input, health, food, look, block}
//                  look: the entity under the crosshair within 6 blocks {id, type, d}; block: the block {type, x, y, z, face}
//                  hand and each hotbar entry: {type, amount} or null; input: {move: {x, y}, jump, sneak} from inputInfo
//                  (air is not in @minecraft/server 2.6.0: no Entity or Player member reports air supply, so it is left out)
//                events: [{type: "break"|"place"|"hurt", tick, dim, x, y, z, block, player, damage, cause}]
//                blocks: [{dim, x, y, z, entry}] the palette entry now at a block a break or place event changed
//                results: [{cmd, ok, successCount, error}] of the commands the last answer carried
//                answered with {scan: [[dim, cx, cz, y0, y1], ...], commands: ["fill ...", {dim, cmd}, ...]}
//   POST /chunk  {dim, cx, cz, y0, y1, height, palette, data, ms, wall, cells}: one band of a chunk, data one character a block,
//                chr(33 + paletteIndex), index ((x * 16) + z) * height + (y - y0); palette entries are "typeId" or
//                "typeId|state=value|..." carrying only the states that change how a block is walked; "?" is a block
//                that could not be read (not loaded). A band whose palette would pass 90 entries goes as two bands of
//                half the height. ms is the script time the band took, wall the ms from its first column to the post,
//                cells the blocks read (the runner keeps them as scan statistics; the band is the same without them).
import { world, system, InputButton, ButtonState } from "@minecraft/server";
import { http, HttpRequest, HttpRequestMethod, HttpHeader } from "@minecraft/server-net";

const RUNNER = "http://127.0.0.1:47812";
const PALETTE_LIMIT = 90;
// the states that change how a block is walked (docs/baritone-port-plan.md, "The probe, version 2"); everything else
// about a block is its typeId's business, decided in baritone_bedrock/world.py
const STATES = ["minecraft:vertical_half", "upside_down_bit", "height", "open_bit", "liquid_depth", "waterlogged"];

// the block families whose walk-relevant states have to be read; everything else is its typeId alone, and skipping
// permutation.getAllStates() on the stone, dirt and air that make up a band is most of the scanner's speed
const STATEFUL = /slab|stairs|snow_layer|door|gate|water|lava/;

function entryOf(block) {
    if (!block) return "?";
    const type = block.typeId;
    if (type === "minecraft:air") return type;
    let entry = type, states = null;
    if (STATEFUL.test(type)) { try { states = block.permutation.getAllStates(); } catch (e) { } }
    if (states) for (const name of STATES) if (name in states) entry += "|" + name + "=" + states[name];
    // Bedrock keeps waterlogging on the Block rather than in the permutation's states
    if (!(states && "waterlogged" in states)) { try { if (block.isWaterlogged) entry += "|waterlogged=true"; } catch (e) { } }
    return entry;
}

function post(path, body) {
    const request = new HttpRequest(RUNNER + path);
    request.method = HttpRequestMethod.Post;
    request.body = JSON.stringify(body);
    request.headers = [new HttpHeader("Content-Type", "application/json")];
    request.timeout = 5;
    return http.request(request);
}

// ------------------------------------------------------------------------------------------------------- terrain
//
// Bands are read in the tick handler under a time budget, not in a system.runJob. A runJob with a yield per column
// got about 17 columns a tick on this server: one 16x16x32 band every 15 ticks, 25 bands in 3.9 s, and the tick rate
// down to 17.4 while it ran, so a planner facing a fresh 100 block corridor (about 20 bands) sat waiting on it. Here
// every tick reads columns of the band at the head of the queue until BUDGET_MS of script time have gone, carries on
// with the next tick, and posts a finished band at once. The /chunk payload carries what the band cost (ms: script
// time spent on it, wall: ms from its first column to the post, cells: blocks read) beside the band itself.

const queue = [];          // bands to read, in the order asked
let current = null;        // the band being read: {dim, cx, cz, y0, y1, height, cells, col, dimension, ms, t0}
const BUDGET_MS = 15;      // of script time a tick; a 50 ms tick keeps 19.5 reports a second or more at this
const COLUMNS_A_STEP = 4;  // columns read between clock checks

function scanSome() {
    const start = Date.now();
    while (Date.now() - start < BUDGET_MS) {
        if (current === null) {
            if (!queue.length) return;
            const [dim, cx, cz, y0, y1] = queue.shift();
            let dimension = null;
            try { dimension = world.getDimension(dim); } catch (e) { }
            const height = y1 - y0 + 1;
            current = { dim, cx, cz, y0, y1, height, cells: new Array(256 * height), col: 0, dimension, ms: 0, t0: Date.now() };
        }
        const c = current, t = Date.now();
        for (let n = 0; n < COLUMNS_A_STEP && c.col < 256; n++, c.col++) {
            const x = c.cx * 16 + (c.col >> 4), z = c.cz * 16 + (c.col & 15);   // index ((x * 16) + z) * height + (y - y0)
            let i = c.col * c.height;
            for (let y = c.y0; y <= c.y1; y++) {
                let block;
                try { block = c.dimension ? c.dimension.getBlock({ x, y, z }) : undefined; } catch (e) { block = undefined; }
                c.cells[i++] = entryOf(block);
            }
        }
        c.ms += Date.now() - t;
        if (c.col >= 256) {
            emit(c.dim, c.cx, c.cz, c.y0, c.y1, c.cells, { ms: c.ms, wall: Date.now() - c.t0, cells: c.cells.length });
            current = null;
        }
    }
}

function emit(dim, cx, cz, y0, y1, cells, stats) {
    const height = y1 - y0 + 1, palette = [], index = new Map(), codes = new Array(cells.length);
    for (let i = 0; i < cells.length; i++) {
        let k = index.get(cells[i]);
        if (k === undefined) { k = palette.length; palette.push(cells[i]); index.set(cells[i], k); }
        codes[i] = k;
    }
    if (palette.length > PALETTE_LIMIT && height > 1) {
        const mid = y0 + (height >> 1) - 1, lower = [], upper = [];   // y0..mid and mid+1..y1, same column order
        for (let col = 0; col < 256; col++)
            for (let y = y0; y <= y1; y++) (y <= mid ? lower : upper).push(cells[col * height + (y - y0)]);
        emit(dim, cx, cz, y0, mid, lower, stats);
        emit(dim, cx, cz, mid + 1, y1, upper, stats);
        return;
    }
    let data = "";
    for (let i = 0; i < codes.length; i++) data += String.fromCharCode(33 + codes[i]);
    post("/chunk", { dim, cx, cz, y0, y1, height, palette, data, ms: stats.ms, wall: stats.wall, cells: stats.cells }).catch(() => { });
}

// -------------------------------------------------------------------------------------------------------- events

const events = [], blocks = [], results = [];

function dimOf(dimension) { return dimension.id.replace("minecraft:", ""); }

world.afterEvents.playerBreakBlock.subscribe((e) => {
    const l = e.block.location, dim = dimOf(e.dimension);
    let type = null;
    try { type = e.brokenBlockPermutation.type.id; } catch (err) { }
    events.push({ type: "break", tick: system.currentTick, dim, x: l.x, y: l.y, z: l.z, block: type, player: e.player.id, damage: null, cause: null });
    blocks.push({ dim, x: l.x, y: l.y, z: l.z, entry: entryOf(e.block) });   // air now, or what a liquid left
});
world.afterEvents.playerPlaceBlock.subscribe((e) => {
    const l = e.block.location, dim = dimOf(e.dimension);
    events.push({ type: "place", tick: system.currentTick, dim, x: l.x, y: l.y, z: l.z, block: e.block.typeId, player: e.player.id, damage: null, cause: null });
    blocks.push({ dim, x: l.x, y: l.y, z: l.z, entry: entryOf(e.block) });
});
world.afterEvents.entityHurt.subscribe((e) => {
    const p = e.hurtEntity, l = p.location;
    let cause = null;
    try { cause = e.damageSource.cause; } catch (err) { }
    events.push({ type: "hurt", tick: system.currentTick, dim: dimOf(p.dimension), x: Math.floor(l.x), y: Math.floor(l.y), z: Math.floor(l.z),
                  block: null, player: p.id, damage: e.damage, cause });
}, { entityTypes: ["minecraft:player"] });

// ------------------------------------------------------------------------------------------------------ commands

function runCommands(list) {
    for (const c of list) {
        const cmd = typeof c === "string" ? c : String((c && c.cmd) || "");
        const dim = (c && typeof c === "object" && c.dim) ? c.dim : "overworld";
        try {
            const r = world.getDimension(dim).runCommand(cmd);
            results.push({ cmd, ok: r.successCount > 0, successCount: r.successCount, error: null });
        } catch (e) {
            results.push({ cmd, ok: false, successCount: 0, error: String(e) });
        }
    }
}

// -------------------------------------------------------------------------------------------------------- players

function item(stack) { return stack ? { type: stack.typeId, amount: stack.amount } : null; }

function report(p) {
    const at = p.location, rot = p.getRotation();
    let v = { x: 0, y: 0, z: 0 }, look = null, block = null, slot = null, hand = null, hotbar = null, input = null, health = null, food = null;
    try { v = p.getVelocity(); } catch (e) { }
    // what the crosshair is on, within reach: the entity, else the block, so the runner can aim by the game's own
    // answer instead of guessing from angles
    try {
        const hit = p.getEntitiesFromViewDirection({ maxDistance: 6 })[0];
        if (hit) look = { id: hit.entity.id, type: hit.entity.typeId, d: hit.distance };
    } catch (e) { }
    try {
        const b = p.getBlockFromViewDirection({ maxDistance: 6 });
        if (b) block = { type: b.block.typeId, x: b.block.location.x, y: b.block.location.y, z: b.block.location.z, face: b.face };
    } catch (e) { }
    try { slot = p.selectedSlotIndex; } catch (e) { }
    try {
        const inventory = p.getComponent("minecraft:inventory");
        const container = inventory ? inventory.container : undefined;
        if (container) {
            hotbar = [];
            for (let i = 0; i < 9; i++) hotbar.push(item(container.getItem(i)));
            if (slot !== null && slot >= 0 && slot < 9) hand = hotbar[slot];
        }
    } catch (e) { }
    try {
        const info = p.inputInfo, m = info.getMovementVector();
        input = { move: { x: m.x, y: m.y }, jump: info.getButtonState(InputButton.Jump) === ButtonState.Pressed,
                  sneak: info.getButtonState(InputButton.Sneak) === ButtonState.Pressed };
    } catch (e) { }
    try { const h = p.getComponent("minecraft:health"); if (h) health = { current: h.currentValue, max: h.effectiveMax }; } catch (e) { }
    try { const f = p.getComponent("minecraft:player.hunger"); if (f) food = { current: f.currentValue, max: f.effectiveMax }; } catch (e) { }
    let sneak = false, sprint = false, jump = false, climbing = false, swimming = false, falling = false, fall = 0, ground = false, water = false;
    try { sneak = p.isSneaking; sprint = p.isSprinting; jump = p.isJumping; climbing = p.isClimbing; swimming = p.isSwimming;
          falling = p.isFalling; fall = p.fallDistance; ground = p.isOnGround; water = p.isInWater; } catch (e) { }
    return { id: p.id, name: p.name, dim: dimOf(p.dimension), x: at.x, y: at.y, z: at.z, yaw: rot.y, pitch: rot.x,
             vx: v.x, vy: v.y, vz: v.z, ground, water, sneak, sprint, jump, climbing, swimming, falling, fall,
             slot, hand, hotbar, input, health, food, look, block };
}

// ----------------------------------------------------------------------------------------------------------- tick

let inFlight = false, quiet = 0;

function unsend(sent) {   // the runner did not take them: keep them for the next report that gets through
    events.unshift(...sent.events); blocks.unshift(...sent.blocks); results.unshift(...sent.results);
}

system.runInterval(() => {
    scanSome();
    if (inFlight) return;   // one report at a time: a slow answer is skipped over, never queued up
    if (quiet > 0) { quiet--; return; }   // the runner is not listening: try again in a second
    const players = [];
    for (const p of world.getPlayers()) { try { players.push(report(p)); } catch (e) { } }
    const sent = { events: events.splice(0), blocks: blocks.splice(0), results: results.splice(0) };
    inFlight = true;
    post("/tick", { tick: system.currentTick, players, events: sent.events, blocks: sent.blocks, results: sent.results }).then((response) => {
        inFlight = false;
        if (response.status !== 200) { quiet = 20; unsend(sent); return; }
        let answer = {};
        try { answer = JSON.parse(response.body || "{}") || {}; } catch (e) { }
        for (const s of answer.scan || []) queue.push(s);
        const commands = answer.commands || [];
        if (commands.length) system.run(() => runCommands(commands));   // inside a tick, never in the promise
    }).catch(() => { inFlight = false; quiet = 20; unsend(sent); });
}, 1);
