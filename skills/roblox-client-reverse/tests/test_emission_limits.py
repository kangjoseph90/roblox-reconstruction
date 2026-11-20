"""Authored regressions for retained closures and sequential local lifetimes."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


TOOLS = os.environ.get("RCR_TEST_TOOLS")


@unittest.skipUnless(TOOLS, "RCR_TEST_TOOLS is required for native behavior tests")
class EmissionLimitsTests(unittest.TestCase):
    def roundtrip(self, source, opt, debug):
        tools = json.loads(Path(TOOLS).read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = root / "original.luau"
            original.write_text(source, encoding="utf-8")
            compiler = tools["compiler"]["argv"][0]
            decoder = tools["decompiler"]["argv"][0]
            runner = tools["runner"]["argv"][0]

            def run(args):
                p = subprocess.run(args, capture_output=True, timeout=30)
                self.assertEqual(p.returncode, 0, p.stderr.decode(errors="replace"))
                return p.stdout

            bytecode = root / "original.luauc"
            bytecode.write_bytes(run([compiler, "--binary", f"-O{opt}", f"-g{debug}", str(original)]))
            recovered = root / "recovered.luau"
            decoded = run([decoder, "--standard", "--no-header", str(bytecode)])
            recovered.write_bytes(decoded)
            # Full compilation, not --only-parse: register exhaustion is a compile error.
            run([compiler, "--binary", str(recovered)])
            expected = run([runner, f"-O{opt}", str(original)]).replace(b"\r\n", b"\n")
            actual = run([runner, f"-O{opt}", str(recovered)]).replace(b"\r\n", b"\n")
            self.assertEqual(actual, expected, decoded.decode(errors="replace"))
            return decoded.decode(errors="replace")

    def test_unused_closures_before_return_and_loop_exits(self):
        source = """
local function result(x)
    local function unused(y) return x + y end
    return x * 2
end
local n = 0
for i = 1, 4 do
    local function unused() return i end
    if i == 3 then break end
    n += result(i)
end
for i = 1, 3 do
    local function unused() return i end
    if i == 2 then continue end
    n += i
end
print(n)
"""
        for opt in (0, 1, 2):
            for debug in (0, 1):
                with self.subTest(opt=opt, debug=debug):
                    self.roundtrip(source, opt, debug)

    def test_sequential_scopes_keep_escaped_mutable_captures(self):
        # Each group reuses VM registers, while recovery produces fresh names.
        # The escaping closures must share their own cell after the do scope exits.
        groups = ["local readers = {}", "local writers = {}"]
        for i in range(130):
            groups.append(f"""do
    local value{i} = tonumber('{i}')
    local function read{i}() return value{i} end
    local function write{i}(x) value{i} = x end
    readers[{i + 1}] = read{i}
    writers[{i + 1}] = write{i}
    value{i} += 1
end""")
        groups.append("local sum = 0\nfor i, read in ipairs(readers) do writers[i](read() + 2); sum += read() end\nprint(sum)")
        for opt in (0, 1, 2):
            for debug in (0, 1):
                with self.subTest(opt=opt, debug=debug):
                    self.roundtrip("\n".join(groups), opt, debug)

    def test_repeat_condition_keeps_body_local_visible(self):
        body = ["local steps = 0", "repeat"]
        for i in range(110):
            body.append(f"local v{i} = tonumber('{i}')")
        body.extend(["steps += 1", "until v109 == 109 and steps == 2", "print(steps)"])
        for opt in (0, 1, 2):
            with self.subTest(opt=opt):
                self.roundtrip("\n".join(body), opt, 1)

    def test_repeated_debug_names_and_shadowing_initializers(self):
        groups = ["local readers = {}", "local value = 7"]
        for i in range(120):
            groups.append(f"""do
    local value = value + tonumber('{i}')
    local function read() return value end
    readers[{i + 1}] = read
    value += 2
end""")
        groups.append("local sum = value\nfor _, read in ipairs(readers) do sum += read() end\nprint(sum)")
        for opt in (0, 1, 2):
            for debug in (0, 1):
                with self.subTest(opt=opt, debug=debug):
                    self.roundtrip("\n".join(groups), opt, debug)


if __name__ == "__main__":
    unittest.main()
