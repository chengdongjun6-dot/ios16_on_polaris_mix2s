"""Fault-inject the actual mode-7 ITCM transaction inside owned CX/XO/buses."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_polaris_gmu_clock import HARNESS as CLOCK_HARNESS
from test_polaris_gmu_tcm import extract_header


def harness():
    # Reuse the checked clock/bus model; extend only GMU memory IO.
    base = CLOCK_HARNESS.split('static unsigned scenarios;', 1)[0]
    base = base.replace('struct sim {', '''struct sim {
 uint32_t tcm[PG_WORDS], original[PG_WORDS], firmware[PG_WORDS], saved[PG_WORDS];
 uint32_t retention, ret_before;
 unsigned tcm_reads, tcm_writes, ret_reads, ret_writes, fw_reads, restore_reads;
 int tcm_fault;
''')
    for name in ('rd', 'wr', 'report', 'init'):
        base = base.replace(' ' + name + '(', ' base_' + name + '(')
    return base + EXTENSION


EXTENSION = r'''
static void accessible(struct sim *s) {
 ck((s->cx & 0x80000001u)==0x80000000u && (s->hw&0x80000000u),"GMU without CX");
 ck((s->gmu&0x80000001u)==1 && (s->cxo&0x80000001u)==1,"GMU without branches");
 for(unsigned i=0;i<3;i++)ck((s->bus[i]&0x80000001u)==1,"GMU without bus");
}
static uint32_t rd(void *p,enum pg_block b,uint32_t o) {
 struct sim *s=p;
 if(b!=PG_GMU){
  if(s->gmu_reads && b==PG_GPUCC){
   if(s->tcm_fault==13 && o==PG_CX_HW)return 0xffffffffu;
   if(s->tcm_fault==14 && o==PG_RCG+4)return 0x602;
  }
  return base_rd(p,b,o);
 }
 accessible(s);
 if(o==PG_RESET){
  if((s->tcm_fault==7 && s->tcm_reads>0 && !s->tcm_writes) ||
     (s->tcm_fault==8 && s->tcm_writes>=256) ||
     (s->tcm_fault==9 && s->fw_reads==PG_WORDS) ||
     (s->tcm_fault==10 && s->tcm_writes>=PG_WORDS+256) ||
     (s->tcm_fault==12 && s->restore_reads==PG_WORDS && s->ret_writes==2))s->reset=0;
  return base_rd(p,b,o);
 }
 ck(s->reset==1,"TCM/retention access after reset uncertainty");
 if(o==PG_RETENTION){
  s->ret_reads++;
  if(s->tcm_fault==3)return 0xffffffffu;
  if(s->tcm_fault==11 && s->fw_reads==PG_WORDS)s->retention|=2;
  return s->retention;
 }
 ck(o>=0x3000 && o<0xb000 && !(o&3u),"GMU read outside documented ITCM");
 unsigned i=(o-0x3000)/4;uint32_t v=s->tcm[i];
 if(!s->tcm_writes){
  s->tcm_reads++;
  if(s->tcm_fault==1 && s->tcm_reads>PG_WORDS && i==17)v^=1;
 }else if(s->tcm_writes==PG_WORDS){
  s->fw_reads++;
  if(s->tcm_fault==4 && i==17)v^=1;
 }else if(s->tcm_writes==2*PG_WORDS){
  s->restore_reads++;
  if(s->tcm_fault==5 && i==17)v^=1;
 }else ck(false,"TCM read during partial write");
 return v;
}
static void wr(void *p,enum pg_block b,uint32_t o,uint32_t v) {
 struct sim *s=p;
 if(b!=PG_GMU){
  if(s->tcm_writes){
   ck(s->restore_reads==PG_WORDS && !memcmp(s->tcm,s->original,sizeof(s->tcm)),"dependency changed before TCM restore verified");
   ck(s->retention==s->ret_before,"dependency changed before retention restored");
  }
  base_wr(p,b,o,v);return;
 }
 accessible(s);ck(s->reset==1,"write after reset uncertainty");
 if(o==PG_RETENTION){
  s->ret_writes++;
  ck(((s->retention^v)&~1u)==0,"unowned retention field changed");
  if(!(v&1u) && s->tcm_writes)
   ck(s->restore_reads==PG_WORDS && !memcmp(s->tcm,s->original,sizeof(s->tcm)),"retention removed before memory restored");
  if(s->tcm_fault==2 || (s->tcm_fault==6 && !(v&1u)))return;
  s->retention=v;return;
 }
 ck(o>=0x3000 && o<0xb000 && !(o&3u),"GMU write outside ITCM (reset forbidden)");
 ck(s->retention&1u,"ITCM write without retention");
 ck(s->tcm_reads==2*PG_WORDS,"write before stable full snapshot");
 unsigned i=(o-0x3000)/4;
 ck(s->tcm_writes<2*PG_WORDS,"unbounded TCM writes");
 ck(i==s->tcm_writes%PG_WORDS,"write skipped/repeated word");
 ck(v==(s->tcm_writes<PG_WORDS?s->firmware[i]:s->original[i]),"wrong write/restore source");
 s->tcm[i]=v;s->tcm_writes++;
}
static void init(struct sim *s){
 base_init(s);s->acquire_bus=true;s->cxo=0x80000001;
 s->bus[0]=0x80004220;s->bus[1]=1;s->bus[2]=0x80000000;
 for(unsigned i=0;i<PG_WORDS;i++){
  s->tcm[i]=s->original[i]=0x10203040u+i;
  s->firmware[i]=0xa5a50000u^i;
 }
 s->firmware[0]=0x43f80;s->firmware[1]=9;
}
static unsigned scenarios;
static void run(struct sim *s,enum pg_cx_result cx,enum pg_itcm_result itcm){
 struct pg_io io={s,rd,wr,wait_us,base_report};
 struct pg_itcm_data t={s->firmware,s->saved,PG_ITCM_NOT_RUN,false};
 memcpy(s->bus_before,s->bus,sizeof(s->bus));s->ret_before=s->retention;
 enum pg_cx_result got=pg_owned_itcm_test(&io,&t);
 if(got!=cx || t.result!=itcm){fprintf(stderr,"tcm_fault=%d clock=%d bus=%d got=%u/%u want=%u/%u\n",s->tcm_fault,s->fault,s->bus_fault,got,t.result,cx,itcm);exit(1);}
 if(cx==PG_CX_RESTORED){
  ck(!t.modified,"safe exit left modification flag");
  ck(s->cx==0x00222001 && s->cmd==0x80000000 && s->cfg==0 && s->gmu==0x80004220,"power/clock restore lost");
  ck(!memcmp(s->tcm,s->original,sizeof(s->tcm)) && s->retention==s->ret_before,"memory/retention restore lost");
  for(unsigned i=0;i<3;i++)ck((s->bus[i]&1u)==(s->bus_before[i]&1u),"bus request lost");
 }else ck(!s->collapse,"CX collapsed after unsafe ITCM result");
 if(cx==PG_CX_HELD_ON && s->tcm_fault){
  ck((s->gmu&1u) && (s->cx&0x80000000u),"unsafe result cut clocks/CX");
  for(unsigned i=0;i<3;i++)ck(s->bus[i]&1u,"unsafe result withdrew bus");
  if(s->tcm_writes)ck(t.modified,"unsafe result discarded original backup");
 }
 if(cx==PG_CX_INPUT_REJECTED)ck(!s->writes && !s->gmu_reads,"bad firmware touched hardware");
 scenarios++;
}
int main(void){
 (void)pg_tcm_test;(void)pg_cx_cycle;(void)pg_cx_status;(void)pg_blob_range_valid;
 (void)pg_clock_probe;(void)pg_bus_clock_probe;
 static struct sim s;
 init(&s);run(&s,PG_CX_RESTORED,PG_ITCM_OK);ck(s.tcm_writes==2*PG_WORDS && s.ret_writes==2,"incomplete normal transaction");
 init(&s);s.retention=1;run(&s,PG_CX_RESTORED,PG_ITCM_OK);ck(s.ret_writes==0,"inherited retention rewritten");
 init(&s);s.retention=0x20;run(&s,PG_CX_RESTORED,PG_ITCM_OK);
 init(&s);s.bus[0]=s.bus[1]=s.bus[2]=1;run(&s,PG_CX_RESTORED,PG_ITCM_OK);ck(!s.bus_writes[0]&&!s.bus_writes[1]&&!s.bus_writes[2],"inherited requests changed");
 const enum pg_itcm_result results[]={PG_ITCM_SNAPSHOT_UNSTABLE,PG_ITCM_RETENTION_REJECTED,PG_ITCM_ACCESS_UNCERTAIN,PG_ITCM_MISMATCH_RESTORED,PG_ITCM_RESTORE_FAILED,PG_ITCM_RESTORE_FAILED,PG_ITCM_RESET_UNCERTAIN,PG_ITCM_RESET_UNCERTAIN,PG_ITCM_RESET_UNCERTAIN,PG_ITCM_RESET_UNCERTAIN,PG_ITCM_RESTORE_FAILED,PG_ITCM_RESET_UNCERTAIN,PG_ITCM_ACCESS_UNCERTAIN,PG_ITCM_ACCESS_UNCERTAIN};
 for(int f=1;f<=14;f++){
  init(&s);s.tcm_fault=f;
  run(&s,(f==2 || f==4)?PG_CX_RESTORED:PG_CX_HELD_ON,results[f-1]);
  if(f<=3 || f==7 || f>=13)ck(!s.tcm_writes,"early failure wrote TCM");
 }
 for(int f=1;f<=3;f++){init(&s);s.bus_fault=f;if(f==3)s.bus[1]=0x80000000;run(&s,PG_CX_RESTORED,PG_ITCM_NOT_RUN);ck(!s.tcm_reads&&!s.ret_reads,"bus timeout accessed TCM");}
 init(&s);s.fault=5;run(&s,PG_CX_RESTORED,PG_ITCM_NOT_RUN);ck(!s.tcm_reads&&!s.ret_reads,"clock timeout accessed TCM");
 init(&s);s.reset=0;run(&s,PG_CX_HELD_ON,PG_ITCM_NOT_RUN);ck(!s.tcm_writes,"running CM3 overwritten");
 init(&s);s.reset=0xffffffffu;run(&s,PG_CX_HELD_ON,PG_ITCM_NOT_RUN);
 init(&s);s.firmware[0]=0;run(&s,PG_CX_INPUT_REJECTED,PG_ITCM_BAD_INPUT);
 init(&s);s.firmware[1]=8;run(&s,PG_CX_INPUT_REJECTED,PG_ITCM_BAD_INPUT);
 init(&s);s.firmware[1]=0x8001;run(&s,PG_CX_INPUT_REJECTED,PG_ITCM_BAD_INPUT);
 struct pg_itcm_data t={s.firmware,s.firmware+1,PG_ITCM_NOT_RUN,false};
 ck(!pg_itcm_input_valid(&t),"overlapping buffers accepted");t.saved=NULL;
 ck(!pg_itcm_input_valid(&t) && !pg_itcm_input_valid(NULL),"null buffers accepted");
 printf("PASS: %u owned ITCM scenarios plus buffer bounds; reset never written\n",scenarios);
 return 0;
}
'''


class OwnedItcmTests(unittest.TestCase):
    def test_transaction_inside_owned_dependencies(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            (directory / 'polaris_gmu_tcm_test.h').write_text(extract_header())
            (directory / 'test.c').write_text(harness())
            result = subprocess.run([os.environ.get('CC', 'cc'), '-std=c11', '-Wall',
                                     '-Wextra', '-Werror', str(directory / 'test.c'),
                                     '-o', str(directory / 'test')], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(directory / 'test')], capture_output=True,
                                    text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('PASS: 27 owned ITCM scenarios', result.stdout)
            print(result.stdout.strip())


if __name__ == '__main__':
    unittest.main()
