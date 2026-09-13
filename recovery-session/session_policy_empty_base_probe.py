import asyncio,json
from httpx import AsyncClient,ASGITransport
from kodezart.main import create_app
from kodezart.core.config import AppConfig
from tests.fakes import FakeJobQueue,FakeAgentRunner,SUPPRESS_ALL_SKILLS
async def run():
 for endpoint in ['fire','workflow']:
  app=create_app();q=FakeJobQueue(events=[]);app.state.job_queue=q;app.state.agent_service=FakeAgentRunner(events=[]);app.state.skills=SUPPRESS_ALL_SKILLS;app.state.config=AppConfig()
  async with AsyncClient(transport=ASGITransport(app=app,raise_app_exceptions=False),base_url='http://test') as client:
   r=await client.post('/api/v1/agent/'+endpoint,json={'prompt':'Inspect','repoPath':'/fixture','baseBranch':''})
  print(json.dumps({'endpoint':endpoint,'status':r.status_code,'body':r.text,'submissions':len(q.submissions)}))
asyncio.run(run())
