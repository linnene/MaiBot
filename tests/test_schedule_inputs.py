from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest

from Mai_life.config import MaiLifeSettings
from Mai_life.core.environment import EnvironmentService
from Mai_life.core.storage import LifeStore
from Mai_life.life.schedule_service import ScheduleService
from Mai_life.tests.support import RecordingLogger, TZ


class RecordingLLM:
    def __init__(self):self.prompts=[]
    def task_available(self,kind):return True
    async def generate_json(self,prompt,system,fallback,**kwargs):
        self.prompts.append(prompt); return fallback


class ScheduleInputsTests(unittest.IsolatedAsyncioTestCase):
    async def test_persona_interests_weather_and_day_are_present_without_rewriting_cached_day(self):
        with TemporaryDirectory() as tmp:
            store=LifeStore(tmp); await store.initialize()
            try:
                llm=RecordingLLM(); logger=RecordingLogger()
                service=ScheduleService(store,MaiLifeSettings(),llm,str(Path(__file__).parents[1]),logger)
                now=datetime(2030,1,2,12,tzinfo=TZ)
                persona="学生，喜欢陶艺和读诗"
                weather="测试城市 雨，4℃；2030-01-02预报：最高8℃，降水概率90%"
                nodes=await service.ensure_day(now,persona,weather)
                prompt=llm.prompts[0]
                for value in (persona,weather,"2030-01-02","不要被参考模板中的爱好覆盖","不是聊天对象的日程","缓存过期"):
                    self.assertIn(value,prompt)
                rhythm_line=prompt.split("参考节奏",1)[1].split("\n",1)[0]
                self.assertNotIn('"summary"',rhythm_line)
                self.assertNotIn('"location"',rhythm_line)
                cached=await service.ensure_day(now,"学生，喜欢编织",weather)
                self.assertEqual(cached,nodes)
                self.assertEqual(len(llm.prompts),1)
                self.assertEqual(service._personality,"学生，喜欢编织")
                self.assertTrue(any("source=template" in value for value in logger.messages("info")))
            finally:await store.close()

    def test_forecast_has_date_units_and_stale_label(self):
        weather={"location_name":"测试城市","description":"雨","temperature":0,"fetched_at":time.time()-10801,
                 "raw_json":{"daily":{"time":["2030-01-02"],"temperature_2m_min":[-2],"temperature_2m_max":[8],
                                        "precipitation_probability_max":[90],"wind_speed_10m_max":[25]}}}
        text=EnvironmentService.weather_text(weather)
        for part in ("0℃","2030-01-02","最低-2℃","最高8℃","90%","25km/h","旧天气缓存"):
            self.assertIn(part,text)
        weather["fetched_at"]=time.time()
        self.assertNotIn("旧天气缓存",EnvironmentService.weather_text(weather))

    def test_unknown_weather_is_not_invented(self):
        self.assertEqual(EnvironmentService.weather_text({}),"天气未知")
