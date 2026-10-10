// emz5/smoke.js — generic SpiderMonkey shell smoke test (E-MZ-5a bootstrap).
// Exercises: wasm module compilation + instantiation, struct/array-style
// allocation on a growable wasm linear memory, JS GC heap churn, repeated
// instantiation, and explicit gc()/gcslice() stress calls.
// Prints "EMZ5-SMOKE OK checksum=<n>" on success.
// Public infra script — generic engine exercise, no target-specific data.

function main() {
  print("EMZ5-SMOKE start");

  // Hand-assembled wasm module:
  //   memory: 1 page, growable; exports f, grow, mem
  //   f(i32)->i32  : store arg at [0], return arg + load([0])
  //   grow(i32)->i32 : return previous page count, grow by arg
  var wasmBytes = new Uint8Array([
    0x00,0x61,0x73,0x6d, 0x01,0x00,0x00,0x00,
    0x01,0x06,0x01,0x60,0x01,0x7f,0x01,0x7f,
    0x03,0x03,0x02,0x00,0x00,
    0x05,0x03,0x01,0x00,0x01,
    0x07,0x12,0x03, 0x01,0x66,0x00,0x00, 0x04,0x67,0x72,0x6f,0x77,0x00,0x01, 0x03,0x6d,0x65,0x6d,0x02,0x00,
    0x0a,0x1a,0x02,
    0x11,0x00, 0x41,0x00,0x20,0x00,0x36,0x02,0x00, 0x20,0x00,0x41,0x00,0x28,0x02,0x00, 0x6a, 0x0b,
    0x06,0x00, 0x20,0x00,0x40,0x00, 0x0b
  ]);

  var mod = new WebAssembly.Module(wasmBytes);
  var inst = new WebAssembly.Instance(mod, {});
  var f = inst.exports.f;
  var grow = inst.exports.grow;
  print("wasm f(12345)=" + f(12345));
  print("wasm grow(2) previous-pages=" + grow(2));

  var mem = inst.exports.mem;

  var ITERS = 4000;
  var checksum = 0;
  var live = [];

  for (var i = 1; i <= ITERS; i++) {
    // "struct" write into wasm linear memory: {a: i, b: i*3, c: i^5}
    var buf = mem.buffer;
    var u8 = new Uint8Array(buf);
    var i32 = new Int32Array(buf);
    var base = (i % 31) * 16;
    i32[base >> 2] = i;
    i32[(base >> 2) + 1] = i * 3;
    i32[(base >> 2) + 2] = i ^ 5;
    checksum += u8[base] + i32[base >> 2];

    // JS array churn
    var arr = [];
    for (var j = 0; j < 24; j++) arr.push(j * i);
    checksum += arr[23] & 0xffff;

    // keep a rolling live set so GC has real work to do
    if (i % 50 === 0) live.push(arr);
    if (live.length > 40) live.splice(0, 20);

    if (i % 400 === 0) {
      var inst2 = new WebAssembly.Instance(mod, {});
      checksum += inst2.exports.f(i);
      if (typeof gc === "function") gc();
      if (typeof gcslice === "function") gcslice(0);
    }
  }

  if (typeof gc === "function") gc();
  print("iterations=" + ITERS + " live-arrays=" + live.length);
  print("EMZ5-SMOKE OK checksum=" + checksum);
  return 0;
}

var rc = main();
if (rc !== 0) throw new Error("smoke rc " + rc);
