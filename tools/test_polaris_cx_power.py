"""Compile the actual CX cycle helper against bounded, fault-injecting IO.

Literal GDSCR/CBCR bits follow MiCode gdsc-regulator.c and gpucc-sdm845.c;
the fake device enforces register access/write scope independently of helpers.
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_polaris_gmu_tcm import extract_header

HARNESS = r'''
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "polaris_gmu_tcm_test.h"
struct sim {
 uint32_t ahb, cx, hw, clock;
 unsigned writes, clock_reads, polls, delay, enables, disables, timing_writes;
 int fault;
};
static void ck(bool b,const char *m){if(!b){fprintf(stderr,"%s\n",m);exit(1);}}
static void power_on(struct sim *s){s->cx|=0x80000000u;s->hw=0x80005ff4u;}
static uint32_t rd(void *p,enum pg_block b,uint32_t o){
 struct sim*s=p;
 if(b==PG_GCC){ck(o==0x71004u,"unexpected GCC register");return s->ahb;}
 ck(b==PG_GPUCC && !(s->ahb&0x80000000u),"access outside reachable GPUCC");
 if(o==0x106cu){
  if(s->enables && !s->disables && s->fault==10)return 0xffffffffu;
  return s->cx;
 }
 if(o==0x1540u){
  ck(s->enables>0,"HW status before owning collapse request");
  if(s->fault==9)return 0xffffffffu;
  if(s->fault==14 && !s->disables && ++s->polls==4)power_on(s);
  return s->hw;
 }
 if(o==0x1098u){
  ck((s->cx&0x80000000u)&&(s->hw&0x80000000u),"clock read before power ACK");
  s->clock_reads++;
  if(s->fault==15)s->cx|=2;
  return s->clock;
 }
 ck(false,"GMU/GX/other GPUCC register accessed");return 0;
}
static void wr(void*p,enum pg_block b,uint32_t o,uint32_t v){
 struct sim*s=p;
 ck(b==PG_GPUCC && o==0x106cu,"write outside CX GDSCR");
 ck(((s->cx^v)&~0x8000f001u)==0,"unowned GDSCR field overwritten");
 ck(!(v&0x80000000u),"read-only PWR_ON replayed");
 s->writes++;ck(s->writes<=4,"repeated power cycle");
 if((s->cx&1u) && (v&1u) && (!s->enables || s->disables)){
  ck(!(s->cx&0x80000006u) && !(s->hw&0x80000000u),"timing write while powered/controlled");
  ck(!s->enables || s->delay>=103,"timing restored before disable delay");
  s->timing_writes++;
  if(s->timing_writes==1){
   ck((v&0xf000u)==0x8000u,"DT wait value not set");
   if(s->fault==17)return;
   s->cx=v;
   if(s->fault==19)s->cx|=2;
  }else{
   ck((v&0xf000u)==0x2000u,"original wait value not restored");
   if(s->fault==18)return;
   s->cx=v;
   if(s->fault==20)power_on(s);
   if(s->fault==21)s->cx|=4;
  }
 }else if(!(v&1u)){
  ck(s->enables++==0,"more than one enable attempt");
  ck((v&0xf000u)==0x8000u,"power transition before DT timing");
  if(s->fault==11)return;
  s->cx=v;
  if(s->fault!=1 && s->fault!=12 && s->fault!=14)power_on(s);
  if(s->fault==5)s->cx|=2;
  if(s->fault==12)s->cx|=0x80000000u;
  if(s->fault==13)s->cx&=~0x80000000u;
  if(s->fault==16)s->cx|=0x10000000u; /* unrelated to wait/control */
  if(s->fault==23)s->cx|=4;
 }else{
  ck(s->enables && s->disables++==0,"invalid collapse restore");
  ck((v&0xf000u)==0x8000u,"DT timing removed before collapse");
  ck(s->delay>=2,"power-down delay missing");
  if(s->fault==6)return;
  s->cx=v;s->hw=0x5ff4u;
  if(s->fault==7)power_on(s);
  if(s->fault==22)s->hw|=0x80000000u;
 }
}
static void wait_us(void*p,uint32_t us){struct sim*s=p;ck(us<=100,"unexpected delay");s->delay+=us;ck(s->delay<=605,"unbounded polling");}
static void report(void*p,const char*t,uint32_t a,uint32_t b){(void)p;(void)t;(void)a;(void)b;}
static void init(struct sim*s){memset(s,0,sizeof(*s));s->ahb=1;s->cx=0x00222001u;s->hw=0x5ff4;s->clock=0x80004220u;}
static unsigned scenarios;
static void run(struct sim*s,enum pg_cx_result want,unsigned writes){
 struct pg_io io={s,rd,wr,wait_us,report};
 enum pg_cx_result got=pg_cx_cycle(&io);
 if(got!=want){fprintf(stderr,"fault %d: result %u expected %u\n",s->fault,got,want);exit(1);}
 ck(s->writes==writes,"unexpected write count");scenarios++;
}
int main(void){(void)pg_bus_clock_probe;(void)pg_clock_probe;struct sim s;(void)pg_tcm_test;(void)pg_cx_status;(void)pg_blob_range_valid;
 init(&s);run(&s,PG_CX_RESTORED,4);ck(s.cx==0x00222001u && s.hw==0x5ff4u && s.clock_reads==1,"normal restore");
 init(&s);s.cx=0x00228001u;run(&s,PG_CX_RESTORED,2);ck(s.cx==0x00228001u && !s.timing_writes,"unneeded timing writes");
 init(&s);s.ahb=0x80000001u;run(&s,PG_CX_AHB_OFF,0);
 init(&s);s.cx=0x80222000u;run(&s,PG_CX_BUSY,0);
 init(&s);s.cx|=2;run(&s,PG_CX_BUSY,0);
 init(&s);s.cx|=4;run(&s,PG_CX_BUSY,0);
 init(&s);s.cx&=~1u;run(&s,PG_CX_BUSY,0);
 init(&s);s.fault=1;run(&s,PG_CX_NO_ACK,4);ck(s.delay==603 && s.clock_reads==0,"no-ACK bound/scope");
 init(&s);s.clock=0x80004221u;run(&s,PG_CX_HELD_ON,2);
 init(&s);s.clock=0x4220u;run(&s,PG_CX_HELD_ON,2);
 init(&s);s.fault=5;run(&s,PG_CX_HELD_ON,2);
 init(&s);s.fault=6;run(&s,PG_CX_RESTORE_FAILED,3);
 init(&s);s.fault=7;run(&s,PG_CX_VOTE_RESTORED_ON,3);ck((s.cx&0xf001u)==0x8001u,"own vote not restored or timing changed while on");
 init(&s);s.fault=9;run(&s,PG_CX_HELD_ON,2);
 init(&s);s.fault=10;run(&s,PG_CX_HELD_ON,2);
 init(&s);s.fault=11;run(&s,PG_CX_NO_ACK,4);
 init(&s);s.fault=12;run(&s,PG_CX_HELD_ON,2);
 init(&s);s.fault=13;run(&s,PG_CX_HELD_ON,2);
 init(&s);s.fault=14;run(&s,PG_CX_RESTORED,4);ck(s.polls==4,"delayed ACK not polled");
 init(&s);s.fault=15;run(&s,PG_CX_HELD_ON,2);
 init(&s);s.fault=16;run(&s,PG_CX_RESTORED,4);ck(s.cx==0x10222001u,"unrelated field lost on restore");
 init(&s);s.fault=17;run(&s,PG_CX_CONFIG_FAILED,2);ck(!s.enables && s.cx==0x00222001u,"transition despite rejected timing");
 init(&s);s.fault=18;run(&s,PG_CX_RESTORE_FAILED,4);
 init(&s);s.fault=19;run(&s,PG_CX_HELD_ON,1);ck(!s.enables,"transition after config takeover");
 init(&s);s.fault=20;run(&s,PG_CX_RESTORE_FAILED,4);
 init(&s);s.fault=21;run(&s,PG_CX_RESTORE_FAILED,4);
 init(&s);s.fault=22;run(&s,PG_CX_VOTE_RESTORED_ON,3);
 init(&s);s.fault=23;run(&s,PG_CX_HELD_ON,2);
 printf("PASS: %u CX cycle scenarios; only collapse/timing writes; DT timing kept through power-down\n",scenarios);return 0;
}
'''


class CxCycleTests(unittest.TestCase):
    def test_actual_power_cycle_core(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'polaris_gmu_tcm_test.h').write_text(extract_header(), encoding='utf8')
            (root / 'test.c').write_text(HARNESS, encoding='utf8')
            exe = root / 'test'
            compiled = subprocess.run([os.environ.get('CC', 'cc'), '-std=c11', '-Wall',
                                       '-Wextra', '-Werror', str(root / 'test.c'),
                                       '-o', str(exe)], capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(exe)], capture_output=True,
                                    text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('PASS: 28 CX cycle scenarios', result.stdout)
            print(result.stdout.strip())


if __name__ == '__main__':
    unittest.main()
