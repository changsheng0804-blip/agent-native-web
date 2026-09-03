# -*- coding: utf-8 -*-
"""GitHub 复杂页面的两次 MCP 读取测量（网络结果仅作参考）。"""
import asyncio, json, sys, time
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
SERVER = str(__import__('pathlib').Path(__file__).resolve().parent / 'server.py')
URL = 'https://github.com/git/git'
async def main():
 p=StdioServerParameters(command=sys.executable,args=[SERVER])
 async with stdio_client(p) as (rd,wr):
  async with ClientSession(rd,wr) as s:
   await s.initialize()
   rows=[]
   for _ in range(2):
    a=time.perf_counter(); o=await asyncio.wait_for(s.call_tool('world_open',{'url':URL,'wait_ms':0,'ready_policy':'action'}),90); b=time.perf_counter(); d=json.loads(o.content[0].text); wid=d['world_id']
    f=await asyncio.wait_for(s.call_tool('world_find',{'world_id':wid,'q':'Pull requests'}),60); c=time.perf_counter(); fd=json.loads(f.content[0].text)
    await s.call_tool('world_close',{'world_id':wid}); rows.append({'open_ms':round((b-a)*1000),'find_ms':round((c-b)*1000),'count':fd.get('count'),'full_scan':d.get('readiness',{}).get('full_scan')})
   print(json.dumps(rows,ensure_ascii=False,indent=2))
asyncio.run(main())
