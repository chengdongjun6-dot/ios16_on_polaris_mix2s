#!/usr/bin/env python3
"""Compile the actual 0096 MMIO transaction core against fault-injecting fake IO.
No physical addresses are mapped and no phone commands are issued.
"""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

PATCH = Path(__file__).resolve().parents[1] / 'xnu/patches/0096-polaris-gmu-tcm-readback.patch'
HARNESS = r'''
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "polaris_gmu_tcm_test.h"
struct sim {
 uint32_t ahb, cx, hw, cxo, gmu, cfg, rcg, retention, reset;
 uint32_t tcm[PG_WORDS], original[PG_WORDS];
 unsigned writes, tcm_writes, waits, resets_read, gpucc_reads, gmu_reads;
 int fail;
};
static void check(bool ok, const char *msg) { if (!ok) { fprintf(stderr,"%s\n",msg);exit(1); } }
static uint32_t rd(void *ctx, enum pg_block b, uint32_t o) {
 struct sim *s=ctx;
 if(b==PG_GCC){ check(o==PG_GCC_AHB,"unexpected GCC read");return s->ahb; }
 check(!(s->ahb&PG_CLK_OFF),"access past config AHB off");
 if(b==PG_GPUCC){
 s->gpucc_reads++;
 if(o!=PG_CX_GDSCR) check((s->cx&0x80000000u) && !(s->cx&1u),"GPUCC read after CX off/collapse");
 switch(o){
 case PG_CX_GDSCR:return s->cx; case PG_CX_HW:return s->hw;
 case PG_CXO_CLK:return s->cxo; case PG_GMU_CLK:return s->gmu;
 case PG_RCG:return s->rcg; case PG_RCG+4:return s->cfg;
 default:check(false,"unexpected GPUCC read");} }
 s->gmu_reads++;
 check(b==PG_GMU && (s->cx&PG_GDSC_PWR_ON) && (s->hw&PG_GDSC_PWR_ON) &&
       !(s->gmu&PG_CLK_OFF) && !(s->cxo&PG_CLK_OFF),"GMU access without clocks/domain");
 if(o==PG_RESET){s->resets_read++;return s->reset;}
 if(o==PG_RETENTION)return s->retention;
 check(o>=PG_ITCM && o<PG_ITCM+PG_WORDS*4u && !(o&3u),"unexpected GMU read");
 uint32_t v=s->tcm[(o-PG_ITCM)/4];
 if(s->fail==5 && s->tcm_writes==PG_WORDS && o==PG_ITCM+400u) v^=1;
 if(s->fail==6 && s->tcm_writes==PG_WORDS*2u && o==PG_ITCM+400u) v^=1;
 return v;
}
static void wr(void *ctx, enum pg_block b, uint32_t o, uint32_t v){
 struct sim *s=ctx;s->writes++;check(b!=PG_GCC,"GCC write forbidden");
 if(b==PG_GPUCC){switch(o){
 case PG_CXO_CLK:s->cxo=(v&~PG_CLK_OFF)|((!(v&1u)||s->fail==2)?PG_CLK_OFF:0);return;
 case PG_GMU_CLK:s->gmu=(v&~PG_CLK_OFF)|((!(v&1u)||s->fail==3)?PG_CLK_OFF:0);return;
 case PG_RCG+4:s->cfg=v;return;
 case PG_RCG:s->rcg=s->fail==1?v:v&~1u;return;
 default:check(false,"write to GDSC/unknown GPUCC register");}}
 check(b==PG_GMU && s->reset==1 && s->resets_read>0,"write before reset proof");
 if(o==PG_RETENTION){if(s->fail!=4)s->retention=v;return;}
 check(o>=PG_ITCM && o<PG_ITCM+PG_WORDS*4u && !(o&3u),"unexpected GMU write, reset release forbidden");
 check((s->retention&1u)!=0,"TCM write without retention");
 s->tcm[(o-PG_ITCM)/4]=v;s->tcm_writes++;
}
static void wait_us(void *ctx,uint32_t n){struct sim*s=ctx;check(n==10,"unexpected delay");s->waits++;check(s->waits<1500,"poll not bounded");}
static void report(void *ctx,const char *tag,uint32_t a,uint32_t b){(void)ctx;(void)tag;(void)a;(void)b;}
static void init(struct sim*s){ memset(s,0,sizeof(*s));s->cx=PG_GDSC_PWR_ON;s->hw=PG_GDSC_PWR_ON;s->gmu=PG_CLK_OFF|2;s->cxo=PG_CLK_OFF|2;s->cfg=0x8602;s->reset=1;
 for(unsigned i=0;i<PG_WORDS;i++)s->tcm[i]=s->original[i]=0x10203040u+i;}
static enum pg_result run(struct sim*s,uint32_t*fw){uint32_t saved[PG_WORDS];struct pg_io io={s,rd,wr,wait_us,report};return pg_tcm_test(&io,fw,saved);}
static void restored(struct sim*s){check(memcmp(s->tcm,s->original,sizeof(s->tcm))==0,"original TCM lost");check(s->cfg==0x8602 && s->cxo==(PG_CLK_OFF|2) && s->gmu==(PG_CLK_OFF|2) && s->retention==0,"state not restored");}
int main(void){struct sim s;uint32_t fw[PG_WORDS];for(unsigned i=0;i<PG_WORDS;i++)fw[i]=i^0xa5a5a5a5u;fw[0]=0x43f80;fw[1]=9;
 (void)pg_cx_cycle; /* separately exercised by test_polaris_cx_power.py */
 /* Actual read-only helper used by mode 2. Literal register values keep
  * the simulated GDSCR semantics independent from the driver's masks. */
 const uint32_t cx_off[]={0x00222001u,0u,0x80000001u};
 for(unsigned i=0;i<3;i++){
  init(&s);s.cx=cx_off[i];
  check(!pg_cx_status(&(struct pg_io){&s,rd,wr,wait_us,report}),"off/collapsing CX treated as on");
  check(s.gpucc_reads==1 && s.gmu_reads==0 && s.writes==0,"read-only CX stop accessed another register");
 }
 init(&s);s.cx=0x80222000u;
 check(pg_cx_status(&(struct pg_io){&s,rd,wr,wait_us,report}),"powered CX status rejected");
 check(s.gpucc_reads==6 && s.gmu_reads==0 && s.writes==0,"powered status probe access scope");
 init(&s);s.cx=0x00222001u;
 check(run(&s,fw)==PG_NO_CX && s.gpucc_reads==1 && s.writes==0,"TCM path read past CX gate");
 init(&s);s.hw=0;
 check(run(&s,fw)==PG_NO_CX && s.gpucc_reads==2 && s.writes==0,"hardware status alias not confirmed");
 check(pg_blob_range_valid(PG_DRAM_BASE,PG_DRAM_SIZE6,PG_DRAM_BASE+0x100000,PG_GMU_BLOB_SIZE),"valid 6GB blob range rejected");
 check(pg_blob_range_valid(PG_DRAM_BASE,PG_DRAM_SIZE8,PG_DRAM_BASE+0x100000,PG_GMU_BLOB_SIZE),"valid 8GB blob range rejected");
 check(!pg_blob_range_valid(0xb0000000ULL,0x4d000000ULL,0xb0100000ULL,PG_GMU_BLOB_SIZE),"low-memory layout incorrectly accepted");
 check(!pg_blob_range_valid(PG_DRAM_BASE,PG_DRAM_SIZE6,PG_DRAM_BASE+PG_DRAM_SIZE6-0x4000,PG_GMU_BLOB_SIZE),"out-of-range blob accepted");
 check(!pg_blob_range_valid(PG_DRAM_BASE,PG_DRAM_SIZE6,PG_DRAM_BASE+0x100001,PG_GMU_BLOB_SIZE),"unaligned blob accepted");
 init(&s);check(run(&s,fw)==PG_OK,"valid test failed");check(s.tcm_writes==PG_WORDS*2,"incomplete TCM transaction");restored(&s);
 init(&s);s.ahb=PG_CLK_OFF;check(run(&s,fw)==PG_NO_AHB && s.writes==0,"AHB gate failed");
 init(&s);s.cx=0;s.hw=0;check(run(&s,fw)==PG_NO_CX && s.writes==0,"CX gate failed");
 init(&s);s.gmu=1;check(run(&s,fw)==PG_BUSY && s.writes==0,"active GMU changed");
 init(&s);s.rcg=1;check(run(&s,fw)==PG_BUSY && s.writes==0,"busy RCG changed");
 init(&s);s.reset=0;check(run(&s,fw)==PG_NOT_RESET && s.tcm_writes==0,"running firmware overwritten");restored(&s);
 init(&s);s.fail=1;check(run(&s,fw)==PG_RESTORE_ERROR && s.tcm_writes==0,"stuck RCG handling failed");
 init(&s);s.fail=2;check(run(&s,fw)==PG_CLOCK_ERROR && s.tcm_writes==0,"CXO timeout failed");restored(&s);
 init(&s);s.fail=3;check(run(&s,fw)==PG_CLOCK_ERROR && s.tcm_writes==0,"GMU clock timeout failed");restored(&s);
 init(&s);s.fail=4;check(run(&s,fw)==PG_RETAIN_ERROR && s.tcm_writes==0,"retention rejection failed");restored(&s);
 init(&s);s.fail=5;check(run(&s,fw)==PG_VERIFY_ERROR,"firmware corruption not detected");restored(&s);
 init(&s);s.fail=6;check(run(&s,fw)==PG_RESTORE_ERROR,"restore corruption not detected");
 init(&s);fw[1]=8;check(run(&s,fw)==PG_BAD_FW && s.writes==0,"invalid vector accepted");fw[1]=9;
 init(&s);s.cxo=3;s.retention=1;check(run(&s,fw)==PG_OK,"inherited CXO case failed");check(s.cxo==3 && s.retention==1,"foreign enable/retention cleared");
 puts("PASS: 14 transaction scenarios, 6 CX gate scenarios, and high-memory blob bounds; no reset, rail, SMMU or CP writes");return 0;
}
'''

def extract_header():
    text = PATCH.read_text(encoding='utf-8')
    block = text.split('+++ b/bsd/dev/polaris_gmu_tcm_test.h\n', 1)[1].split('--- ', 1)[0]
    return '\n'.join(line[1:] for line in block.splitlines() if line.startswith('+')) + '\n'

class GmuTransactionTests(unittest.TestCase):
    def test_actual_transaction_core(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p/'polaris_gmu_tcm_test.h').write_text(extract_header(), encoding='utf-8')
            (p/'test.c').write_text(HARNESS, encoding='utf-8')
            exe=p/'test'
            subprocess.run([os.environ.get('CC','cc'),'-std=c11','-Wall','-Wextra','-Werror',str(p/'test.c'),'-o',str(exe)],check=True,capture_output=True,text=True)
            result=subprocess.run([str(exe)],check=True,capture_output=True,text=True,timeout=10)
            self.assertIn('PASS: 14 transaction scenarios, 6 CX gate scenarios, and high-memory blob bounds', result.stdout)
            print(result.stdout.strip())

if __name__=='__main__': unittest.main()
