"""Run the patched FT polling loop and frame/HID code past its old lifetime."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PATCHES = ROOT / 'xnu/patches'


def patched_source(directory):
    patch = (PATCHES / '0094-polaris-focaltech-ft8716-touch.patch').read_text()
    added = patch.split('@@ -0,0 +1,407 @@\n', 1)[1]
    source = ''.join(line[1:] + '\n' for line in added.splitlines() if line.startswith('+'))
    target = directory / 'bsd/dev/polaris_touch_ft.c'
    target.parent.mkdir(parents=True)
    target.write_text(source)
    for name in ('0095-polaris-focaltech-idle-frame.patch',
                 '0097-polaris-focaltech-persistent-poll.patch'):
        subprocess.run(['git', 'apply', str(PATCHES / name)], cwd=directory, check=True)
    return target.read_text()


PREAMBLE = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#define FT_MAX_FINGERS 10u
#define FT_FRAME_BYTES 63u
#define FT_X_MAX 1080u
#define FT_Y_MAX 2160u
static bool ft_down[FT_MAX_FINGERS], hid_down;
static int ft_x[FT_MAX_FINGERS], ft_y[FT_MAX_FINGERS];
static unsigned reads, waits, reports, limit, scenario;
static void sweet_hid_touch(int finger, int down, int x, int y) {
    assert(finger == 0 && x == 10 && y == 20);
    hid_down = down != 0;
    reports++;
}
static void ft_wait(unsigned ms) { assert(ms == 20); waits++; }
static bool ft_read(uint8_t reg, uint8_t *buf, unsigned len) {
    assert(reg == 0 && len == FT_FRAME_BYTES);
    assert(++reads < 13000); /* fail promptly if the bad-frame exit disappears */
    memset(buf, 0, len);
    buf[2] = 1; buf[4] = 10; buf[6] = 20;
    bool bad = reads > limit || (scenario == 2 && reads >= 2 && reads <= 8);
    if (scenario == 1 && bad) { buf[0] = 0x10; return true; }
    if (scenario == 3 && !bad) { /* observed FT8716 idle sentinel */
        memset(buf + 1, 0xff, 6);
    }
    return !bad;
}
'''

MAIN = r'''
int main(void) {
    for (scenario = 0; scenario < 4; ++scenario) {
        reads = waits = reports = 0; hid_down = false;
        memset(ft_down, 0, sizeof(ft_down));
        limit = scenario == 2 ? 15 : 12500;
        poll();
        assert(reads == limit + 8); /* eight consecutive failures, never lifetime */
        assert(waits == reads - 1); /* final failure exits without another delay */
        assert(!hid_down && !ft_down[0]);
        assert(reports == (scenario == 3 ? 0u : scenario == 2 ? 4u : 2u));
    }
    puts("PASS: sustained contact, malformed frames, transient failures, idle past 12000");
    return 0;
}
'''


class TouchLifetimeTests(unittest.TestCase):
    def test_actual_poll_loop_and_release(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            source = patched_source(directory)
            functions = source[source.index('static void\nft_release(void)'):
                               source.index('static void\nft_restore_bus(void)')]
            loop = source[source.index('\tunsigned failures = 0;'):source.index('\nrestore:')]
            code = PREAMBLE + functions + '\nstatic void poll(void) {\n' \
                + '\tuint8_t frame[FT_FRAME_BYTES];\n' + loop + '\n}\n' + MAIN
            (directory / 'test.c').write_text(code)
            subprocess.run([os.environ.get('CC', 'cc'), '-std=c11', '-Wall', '-Wextra',
                            '-Werror', str(directory / 'test.c'), '-o', str(directory / 'test')],
                           check=True, capture_output=True, text=True)
            result = subprocess.run([str(directory / 'test')], capture_output=True,
                                    text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.count('polling continues past 12000 samples'), 3)
            self.assertIn('PASS:', result.stdout)
            print(result.stdout.splitlines()[-1])


if __name__ == '__main__':
    unittest.main()
