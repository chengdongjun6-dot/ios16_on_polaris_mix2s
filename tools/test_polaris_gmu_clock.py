"""Exercise actual mode-5/6 clocks, bus ownership and CX rollback with IO faults."""
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
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "polaris_gmu_tcm_test.h"
struct sim {
 bool acquire_bus;
 uint32_t bus_before[3];
 unsigned bus_writes[3], bus_reads[3], bus_restored, bus_ready;
 int bus_fault;
 uint32_t cx, hw, gmu, cxo, cmd, cfg, reset, bus[3];
 unsigned writes, collapse, updates, timing, delay, gmu_reads, ready, verified;
 int fault;
};
static void ck(bool b,const char*m){if(!b){fprintf(stderr,"%s\n",m);exit(1);}}
static void root_status(struct sim*s){
 if((s->cmd&2u)||(s->gmu&1u))s->cmd&=~0x80000000u;
 else s->cmd|=0x80000000u;
 if(s->fault==1)s->cmd|=0x80000000u;
}
static int bus_index(uint32_t o){switch(o){case 0x7100c:return 0;case 0x71018:return 1;case 0x44038:return 2;default:return -1;}}
static uint32_t rd(void*p,enum pg_block b,uint32_t o){
 struct sim*s=p;
 if(b==PG_GCC){
  if(o==0x71004)return 1;
  int i=bus_index(o);ck(i>=0,"unexpected GCC read");s->bus_reads[i]++;
  if(s->bus_fault==10 && i==0)return 0xffffffffu;
  if(s->bus_fault==11 && i==1 && s->bus_reads[i]>1)s->bus[i]&=~1u;
  return s->bus[i];
 }
 if(b==PG_GMU){
  ck(o==0x14000u,"non-reset GMU register accessed");
  ck((s->cx&0x80000000u) && !(s->cx&1u) && (s->gmu&0x80000001u)==1 && (s->cxo&0x80000001u)==1,"GMU read before power/clocks");
  ck(!((s->bus[0]|s->bus[1]|s->bus[2])&0x80000000u),"GMU read before bus proof");
  s->gmu_reads++;return s->reset;
 }
 ck(b==PG_GPUCC,"unknown IO block");
 if(o==0x106c)return s->cx;
 if(o==0x1540)return s->hw;
 ck((s->cx&0x80000000u) && !(s->cx&1u),"GPUCC clock access while CX off");
 switch(o){
 case 0x1098:return s->gmu;
 case 0x109c:return s->fault==19?0xffffffffu:s->cxo;
 case 0x1120:
  if((s->cmd&1u) && s->fault!=2 && !(s->fault==7 && s->updates==2))s->cmd&=~1u;
  return s->cmd;
 case 0x1124:return s->cfg;
 default:ck(false,"unexpected GPUCC read");return 0;
 }
}
static void wr(void*p,enum pg_block b,uint32_t o,uint32_t v){
 struct sim*s=p;s->writes++;ck(s->writes<40,"unbounded writes");
 if(b==PG_GCC){
  int i=bus_index(o);ck(s->acquire_bus && i>=0,"unapproved GCC write");
  ck(!(s->bus_before[i]&1u),"inherited bus request changed");
  ck(!(v&0xf0000000u) && ((s->bus[i]^v)&~0xf0000001u)==0,"bus status replay or unowned field write");
  s->bus_writes[i]++;
  if(v&1u){
   if(s->bus_fault==4 && i==0)return;
   s->bus[i]=v;
   if((s->bus_fault==1 && i==2)||(s->bus_fault==2 && i==0)||(s->bus_fault==3 && i==1))s->bus[i]|=0x80000000u;
   if(s->bus_fault==9 && i==0)s->bus[i]|=0x10000u;
  }else{
   if((s->bus_fault==5 && i==2)||(s->bus_fault==6 && i==0))return;
   s->bus[i]=v;
   if(i==1){if(s->bus_fault!=7)s->bus[i]|=0x80000000u;}
   else if((s->bus_before[i]&0x80000000u) && !(s->bus_fault==8 && i==0))s->bus[i]|=0x80000000u;
  }
  return;
 }
 ck(b==PG_GPUCC,"GMU write forbidden");
 if(o==0x106c){
  ck(((s->cx^v)&~0x8000f001u)==0 && !(v&0x80000000u),"unowned CX write");
  if(!(v&1u)){
   ck((v&0xf000u)==0x8000u,"CX DT timing missing");
   s->cx=v|0x80000000u;s->hw=0x80001fe0u;
   if(s->cxo&1u)s->cxo&=~0x80000000u;
  }else{
   if(s->cx&0x80000000u){
    ck((s->gmu&0x80000001u)==0x80000000u,"CX collapsed with GMU running");
    ck(s->cmd==0x80000000u && s->cfg==0,"CX collapsed before RCG restored");
    ck((s->gmu&0xff0u)==0x220u,"CX collapsed before timing restored");
    s->collapse++;
   }
   s->cx=v;s->hw=0x5ff4u;s->cxo|=0x80000000u;
  }
  return;
 }
 ck((s->cx&0x80000000u) && !(s->cx&1u),"clock write without CX");
 if(o==0x1098){
  ck(!(v&0xf0000000u) && ((s->gmu^v)&~0xf0000ff1u)==0,"unowned CBCR write or status replay");
  if((s->gmu^v)&0xff0u){
   ck((s->gmu&0x80000001u)==0x80000000u,"timing changed with active/requested clock");
   s->timing++;
   if((s->fault==10 && s->timing==1)||(s->fault==11 && s->timing==2))return;
  }
  if((v&1u)){
   ck(s->cfg==1u && (v&0xff0u)==0xff0u,"GMU enabled before XO/timing config");
   if(s->fault==5){s->gmu=v|0x80000000u;return;}
  }else if(s->gmu&1u){if(s->fault==6)return;}
  s->gmu=v|((v&1u)?0:0x80000000u);
  if(s->fault==18)s->gmu|=0x2000u;
  root_status(s);return;
 }
 if(o==0x109c){
  ck(!(v&0xf0000000u) && ((s->cxo^v)&~0xf0000001u)==0,"unowned CXO write");
  if(!(v&1u) && (s->cxo&1u) && s->fault==9)return;
  s->cxo=v|((!(v&1u)||s->fault==4)?0x80000000u:0);return;
 }
 if(o==0x1120){
  ck(!(v&~3u),"RCG status/reserved bit replayed");
  if((s->cmd&2u) && !(v&2u) && s->fault==8)return;
  if(v&1u){ck(s->cmd&2u,"config update without force-enable");s->updates++;}
  s->cmd=v;root_status(s);return;
 }
 if(o==0x1124){
  ck((s->cmd&0x80000002u)==2u,"config write without live forced root");
  ck(!(s->gmu&1u),"RCG changed with active branch");
  ck(v==0u||v==1u,"PLL or non-XO requested");
  if(s->fault!=3)s->cfg=v;
  return;
 }
 ck(false,"write outside approved clocks/CX fields");
}
static void wait_us(void*p,uint32_t n){struct sim*s=p;ck(n<=100,"unexpected delay");s->delay+=n;ck(s->delay<4000,"poll unbounded");}
static void report(void*p,const char*t,uint32_t a,uint32_t b){struct sim*s=p;(void)b;
 if(!strcmp(t,"bus probe ready check (a=offset)"))s->bus_ready++;
 if(!strcmp(t,"bus probe requests restored (a=reset-verified)"))s->bus_restored++;
 if(!strcmp(t,"clock probe XO ready"))s->ready++;
 if(!strcmp(t,"clock probe restored (a=reset-verified)"))s->verified=a;
}
static void init(struct sim*s){memset(s,0,sizeof(*s));s->cx=0x00222001;s->hw=0x5ff4;s->gmu=0x80004220;s->cxo=0x80000000;s->cmd=0x80000000;s->reset=1;}
static unsigned scenarios;
static void run(struct sim*s,enum pg_cx_result want,unsigned reads){
 memcpy(s->bus_before,s->bus,sizeof(s->bus));
 struct pg_io io={s,rd,wr,wait_us,report};
 enum pg_cx_result got=pg_cx_cycle_with_probe(&io,s->acquire_bus?pg_bus_clock_probe:pg_clock_probe);
 if(got!=want){fprintf(stderr,"fault=%d got=%d want=%d\n",s->fault,got,want);exit(1);}
 ck(s->gmu_reads==reads,"unexpected GMU read count");
 if(want==PG_CX_RESTORED){ck(s->cx==0x00222001 && s->hw==0x5ff4 && s->cmd==0x80000000 && s->cfg==0,"state not restored");}
 else ck(!s->collapse,"CX collapsed after unsafe result");
 if(s->acquire_bus && want==PG_CX_RESTORED){for(unsigned i=0;i<3;i++)ck((s->bus[i]&1u)==(s->bus_before[i]&1u),"bus request not restored");}
 scenarios++;
}
int main(void){(void)pg_bus_clock_probe;struct sim s;(void)pg_tcm_test;(void)pg_cx_cycle;(void)pg_cx_status;(void)pg_blob_range_valid;
 init(&s);run(&s,PG_CX_RESTORED,1);ck(s.verified==1 && s.ready==1 && s.gmu==0x80004220 && s.cxo==0x80000000,"normal restore failed");
 init(&s);s.cxo=0x80000001;run(&s,PG_CX_RESTORED,1);ck(s.cxo==0x80000001,"inherited CXO lost");
 for(unsigned i=0;i<3;i++){init(&s);s.bus[i]=0x80000000;run(&s,PG_CX_RESTORED,0);ck(s.ready==1 && !s.verified,"bus gate result");}
 init(&s);s.fault=1;run(&s,PG_CX_RESTORED,0);ck(!s.ready,"root timeout progressed");
 init(&s);s.fault=2;run(&s,PG_CX_HELD_ON,0);
 init(&s);s.fault=3;run(&s,PG_CX_RESTORED,0);
 init(&s);s.fault=4;run(&s,PG_CX_RESTORED,0);
 init(&s);s.fault=5;run(&s,PG_CX_RESTORED,0);
 init(&s);s.fault=6;run(&s,PG_CX_HELD_ON,1);
 init(&s);s.fault=7;run(&s,PG_CX_HELD_ON,1);
 init(&s);s.fault=8;run(&s,PG_CX_HELD_ON,0);
 init(&s);s.fault=9;run(&s,PG_CX_HELD_ON,1);
 init(&s);s.fault=10;run(&s,PG_CX_RESTORED,0);
 init(&s);s.fault=11;run(&s,PG_CX_HELD_ON,1);
 init(&s);s.gmu=0x4221;run(&s,PG_CX_HELD_ON,0);
 init(&s);s.cmd=0x80000002;run(&s,PG_CX_HELD_ON,0);
 init(&s);s.cfg=0x600;run(&s,PG_CX_HELD_ON,0);
 init(&s);s.cfg=0x100000;run(&s,PG_CX_HELD_ON,0);
 init(&s);s.reset=0;run(&s,PG_CX_HELD_ON,1);
 init(&s);s.reset=0xffffffff;run(&s,PG_CX_HELD_ON,1);
 init(&s);s.fault=18;run(&s,PG_CX_RESTORED,1);ck(s.gmu&0x2000u,"unrelated branch bit lost");
 init(&s);s.fault=19;run(&s,PG_CX_HELD_ON,0);
 init(&s);s.acquire_bus=true;run(&s,PG_CX_RESTORED,1);ck(s.bus_restored==1 && s.bus_ready==3 && s.verified==1,"bus readiness/reset/restore result");
 init(&s);s.acquire_bus=true;s.bus[0]=s.bus[1]=s.bus[2]=1;run(&s,PG_CX_RESTORED,1);ck(s.bus_writes[0]+s.bus_writes[1]+s.bus_writes[2]==0,"inherited buses written");
 init(&s);s.acquire_bus=true;s.bus[0]=0x80004220;s.bus[1]=1;run(&s,PG_CX_RESTORED,1);ck(s.bus_writes[1]==0 && s.bus_writes[0]==2 && s.bus_writes[2]==2,"owned request scope");
 init(&s);s.acquire_bus=true;s.bus[0]=0x80004220;s.bus[1]=0x80000001;run(&s,PG_CX_RESTORED,0);ck(s.bus[1]==0x80000001 && s.bus_restored==1,"inherited halted SNOC toggled");
 init(&s);s.acquire_bus=true;s.bus_fault=1;run(&s,PG_CX_RESTORED,0);ck(s.bus_writes[0]+s.bus_writes[1]==0,"continued after AXI timeout");
 init(&s);s.acquire_bus=true;s.bus_fault=2;run(&s,PG_CX_RESTORED,0);ck(s.bus_writes[1]==0,"continued after MEMNOC timeout");
 init(&s);s.acquire_bus=true;s.bus_fault=3;run(&s,PG_CX_RESTORED,0);
 init(&s);s.acquire_bus=true;s.bus_fault=4;run(&s,PG_CX_RESTORED,0);
 init(&s);s.acquire_bus=true;s.bus_fault=5;run(&s,PG_CX_HELD_ON,1);
 init(&s);s.acquire_bus=true;s.bus_fault=6;run(&s,PG_CX_HELD_ON,1);
 init(&s);s.acquire_bus=true;s.bus_fault=7;run(&s,PG_CX_HELD_ON,1);
 init(&s);s.acquire_bus=true;s.bus[0]=0x80004220;s.bus_fault=8;run(&s,PG_CX_RESTORED,1);ck(s.bus[0]==0x4220,"shared voted clock forced off");
 init(&s);s.acquire_bus=true;s.bus_fault=9;run(&s,PG_CX_RESTORED,1);ck(s.bus[0]&0x10000u,"unrelated bus field lost");
 init(&s);s.acquire_bus=true;s.bus_fault=10;run(&s,PG_CX_RESTORED,0);ck(s.bus_writes[0]+s.bus_writes[1]+s.bus_writes[2]==0,"write after unreadable snapshot");
 init(&s);s.acquire_bus=true;s.bus[1]=1;s.bus_fault=11;run(&s,PG_CX_HELD_ON,0);
 init(&s);s.acquire_bus=true;s.reset=0;run(&s,PG_CX_HELD_ON,1);ck(s.bus_restored==0 && (s.bus[0]&s.bus[1]&s.bus[2]&1u),"bus cut after unknown CM3");
 init(&s);s.acquire_bus=true;s.reset=0xffffffff;run(&s,PG_CX_HELD_ON,1);
 printf("PASS: %u clock/CX/bus scenarios; no GMU writes; owned GCC requests and ordered restoration enforced\n",scenarios);return 0;
}
'''

class ClockProbeTests(unittest.TestCase):
    def test_actual_clock_probe_inside_cx_cycle(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)
            (p/'polaris_gmu_tcm_test.h').write_text(extract_header(),encoding='utf8')
            (p/'test.c').write_text(HARNESS,encoding='utf8')
            compiled = subprocess.run([os.environ.get('CC','cc'),'-std=c11','-Wall','-Wextra','-Werror',str(p/'test.c'),'-o',str(p/'test')],capture_output=True,text=True)
            self.assertEqual(compiled.returncode,0,compiled.stderr)
            run = subprocess.run([str(p/'test')],capture_output=True,text=True,timeout=10)
            self.assertEqual(run.returncode,0,run.stderr)
            self.assertIn('PASS: 41 clock/CX/bus scenarios',run.stdout)
            print(run.stdout.strip())
if __name__ == '__main__':unittest.main()
