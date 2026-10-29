from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs,urlsplit
import unittest

from Mai_life.config import MaiLifeSettings,SearchProviderProfile
from Mai_life.information.playwright_search import BingSearchParser,DuckDuckGoSearchParser,PlaywrightSearchClient
from Mai_life.core.storage import LifeStore
from Mai_life.information.search_models import SearchBackendError,SearchResponse,SearchResult
from Mai_life.information.search_parsing import is_weak_result
from Mai_life.information.information_service import InformationService
from Mai_life.information.search_service import SearchService
from pydantic import ValidationError


class ProviderConfigurationTests(unittest.TestCase):
    def test_default_search_chain_prefers_playwright_without_key(self):
        config=MaiLifeSettings(); profile=config.search_api.providers[0]
        self.assertTrue(profile.enabled)
        self.assertEqual(profile.provider_type,"playwright")
        self.assertEqual(profile.browser_engine,"bing")
        self.assertTrue(profile.headless)
        self.assertEqual(profile.api_keys,[])

    def test_playwright_provider_rejects_api_keys(self):
        with self.assertRaisesRegex(ValidationError,"Playwright"):
            SearchProviderProfile(enabled=True,provider_type="playwright",api_keys=["secret"])

class DummyLogger:
    def __getattr__(self,name):return lambda *args,**kwargs:None


class FakeBrowserSearch:
    def __init__(self,error:SearchBackendError|None=None)->None:
        self.error=error; self.calls:list[dict[str,object]]=[]; self.closed=False
    async def search(self,query:str,**options:object)->SearchResponse:
        self.calls.append({"query":query,**options})
        if self.error is not None:raise self.error
        return SearchResponse(results=[SearchResult(title="浏览器结果",url="https://example.com/browser",snippet="浏览器摘要")],
                              provider_type="playwright",model=str(options["engine"]),cited=True)
    async def close(self)->None:self.closed=True


class FakeHTTPResponse:
    status=200; headers:dict[str,str]={}

    @staticmethod
    def json()->dict[str,object]:
        return {"code":200,"data":{"webPages":{"value":[{"name":"API 备援","url":"https://example.com/api","summary":"API 摘要"}]}}}


class FakeBochaHTTP:
    def __init__(self)->None:self.calls:list[str]=[]
    async def post_json(self,url:str,payload:object,**options:object)->FakeHTTPResponse:
        self.calls.append(url); del payload,options
        return FakeHTTPResponse()


class SearchServicePlaywrightTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self)->None:
        self.tmp=tempfile.TemporaryDirectory(); self.store=LifeStore(self.tmp.name); await self.store.initialize()

    async def asyncTearDown(self)->None:
        await self.store.close(); self.tmp.cleanup()

    async def test_playwright_provider_searches_without_key_and_records_browser_fingerprint(self):
        config=MaiLifeSettings(); client=FakeBrowserSearch()
        service=SearchService(config,HttpClientDummy(),self.store,DummyLogger(),playwright_client=client)
        response=await service.search("人工智能",operation="tool_search",freshness="day")
        self.assertEqual(response.provider_type,"playwright"); self.assertEqual(response.model,"bing")
        self.assertEqual(client.calls,[{"query":"人工智能","engine":"bing","freshness":"day","timeout_seconds":12.0,"max_results":5}])
        rows=list(self.store.conn.execute("SELECT provider_type,key_fingerprint,error_class FROM search_api_events"))
        self.assertEqual(rows[0]["provider_type"],"playwright"); self.assertEqual(rows[0]["key_fingerprint"],"browser"); self.assertEqual(rows[0]["error_class"],"")
        health=await service.health_snapshot(); self.assertEqual(health[0]["browser_engine"],"bing"); self.assertTrue(health[0]["headless"])
        await service.close(); self.assertTrue(client.closed)

    async def test_browser_failure_fails_over_to_api_provider(self):
        config=MaiLifeSettings()
        config.search_api.providers=[
            SearchProviderProfile(enabled=True,provider_type="playwright"),
            SearchProviderProfile(enabled=True,provider_type="bocha",api_keys=["good"]),
        ]
        browser=FakeBrowserSearch(SearchBackendError("浏览器搜索超时",error_class="network")); http=FakeBochaHTTP()
        service=SearchService(config,http,self.store,DummyLogger(),playwright_client=browser)
        response=await service.search("人工智能",operation="search")
        self.assertEqual(response.provider_type,"bocha"); self.assertEqual(response.results[0].title,"API 备援")
        self.assertEqual(len(http.calls),1)
        browser_runtime=await self.store.get_search_key_runtime(service.providers()[0][0],"browser")
        self.assertEqual(browser_runtime["status"],"service_error"); self.assertEqual(browser_runtime["last_error_class"],"network")


class HttpClientDummy:
    def post_json(self,*args:object,**kwargs:object)->None:raise AssertionError("浏览器搜索成功时不应调用 API")

class StubClosingSearch:
    def __init__(self)->None:self.closed=False
    async def close(self)->None:self.closed=True


class InformationCloseTests(unittest.IsolatedAsyncioTestCase):
    async def test_information_close_releases_browser_search(self):
        config=MaiLifeSettings(); information=InformationService(None,None,config,None,DummyLogger())
        information.search=StubClosingSearch()
        await information.close()
        self.assertTrue(information.search.closed)

class SearchResultParserTests(unittest.TestCase):
    def test_bing_parser_extracts_organic_redirect_and_snippet(self):
        parser=BingSearchParser()
        html=(Path(__file__).parent / "fixtures" / "bing_search.html").read_text(encoding="utf-8")
        results=parser.parse(html,5)
        self.assertEqual([item.title for item in results[:2]],["人工智能 - Microsoft","人工智能报告"])
        self.assertEqual(results[0].url,"https://www.microsoft.com/ai")
        self.assertIn("人工智能技术",results[0].snippet)
        self.assertTrue(all("bing.com/ck/a" not in item.url for item in results))
        self.assertTrue(all(item.url.startswith("https://") for item in results))

    def test_bing_parser_respects_limit_and_filters_invalid_urls(self):
        results=BingSearchParser().parse((Path(__file__).parent / "fixtures" / "bing_search.html").read_text(encoding="utf-8"),2)
        self.assertEqual(len(results),2)
        self.assertNotIn("无效协议",[item.title for item in results])

    def test_duckduckgo_parser_extracts_results_and_keeps_missing_snippet(self):
        results=DuckDuckGoSearchParser().parse((Path(__file__).parent / "fixtures" / "duckduckgo_search.html").read_text(encoding="utf-8"),5)
        self.assertEqual(len(results),3)
        self.assertEqual(results[0].title,"DuckDuckGo 人工智能")
        self.assertEqual(results[0].url,"https://example.com/ddg")
        self.assertIn("搜索结果摘要",results[0].snippet)
        self.assertEqual(results[2].snippet,"")

    def test_duckduckgo_parser_respects_limit(self):
        results=DuckDuckGoSearchParser().parse((Path(__file__).parent / "fixtures" / "duckduckgo_search.html").read_text(encoding="utf-8"),1)
        self.assertEqual([item.url for item in results],["https://example.com/ddg"])


class PlaywrightSearchUrlTests(unittest.TestCase):
    def test_search_url_encodes_query_and_filters(self):
        bing=PlaywrightSearchClient.search_url("bing","人工智能 新闻","day")
        duck=PlaywrightSearchClient.search_url("duckduckgo","人工智能 新闻","week")
        self.assertIn("q=%E4%BA%BA%E5%B7%A5%E6%99%BA%E8%83%BD+%E6%96%B0%E9%97%BB",bing)
        self.assertIn("filters=ex1%3A%22ez5_19700_19701%22",bing)
        self.assertIn("q=%E4%BA%BA%E5%B7%A5%E6%99%BA%E8%83%BD+%E6%96%B0%E9%97%BB",duck)
        self.assertIn("df=w",duck)

    def test_unknown_engine_has_explicit_error(self):
        with self.assertRaisesRegex(SearchBackendError,"不支持的浏览器搜索引擎"):
            PlaywrightSearchClient.search_url("google","人工智能","any")

    def test_search_url_normalizes_engine_case_and_whitespace(self):
        # search_url 与解析器选择共用同一归一化，"Bing"/" DuckDuckGo " 不得走错分支。
        self.assertEqual(PlaywrightSearchClient.search_url("Bing","人工智能","any"),
                         PlaywrightSearchClient.search_url("bing","人工智能","any"))
        self.assertIn("html.duckduckgo.com",PlaywrightSearchClient.search_url(" DuckDuckGo ","人工智能","any"))


class BingQueryShapingTests(unittest.TestCase):
    def test_bing_query_drops_interrogative_fillers(self):
        # 实测：整形后"明天天气"仍返回 5 个天气站点（2026-10-17 探针），不误伤反例。
        self.assertEqual(_bing_q(PlaywrightSearchClient.search_url("bing","明天天气怎么样","any")),"明天天气")
        # 段尾冗余词剔除后不足 3 字时放弃剔除，保留原查询（"是 开源软件"是被子串剔除打碎的输出，曾锁定缺陷）。
        self.assertEqual(_bing_q(PlaywrightSearchClient.search_url("bing","什么是 开源软件","any")),"什么是 开源软件")

    def test_bing_query_keeps_question_words_intact(self):
        # 疑问词里的"什么/怎么"不得按子串剔除：否则"为什么"变"为"、"什么意思"变"意思"，失配反而加重。
        for query in ("为什么","怎么办","什么意思","什么是 开源软件","什么是开源软件","怎么 办"):
            with self.subTest(query=query):
                self.assertEqual(_bing_q(PlaywrightSearchClient.search_url("bing",query,"any")),query)

    def test_bing_query_keeps_plain_queries_unchanged(self):
        # 无冗余词的查询原样透传，既有 URL 断言不受影响。
        self.assertEqual(_bing_q(PlaywrightSearchClient.search_url("bing","人工智能 新闻","day")),"人工智能 新闻")
        self.assertEqual(_bing_q(PlaywrightSearchClient.search_url("bing","开源软件","any")),"开源软件")
        self.assertEqual(_bing_q(PlaywrightSearchClient.search_url("bing","最近的中文开源模型发布","any")),"最近的中文开源模型发布")

    def test_bing_query_all_filler_falls_back_to_original(self):
        self.assertEqual(_bing_q(PlaywrightSearchClient.search_url("bing","怎么样","any")),"怎么样")

    def test_duckduckgo_query_is_not_shaped(self):
        self.assertEqual(_bing_q(PlaywrightSearchClient.search_url("duckduckgo","明天天气怎么样","any")),"明天天气怎么样")


class FakeRequest:
    def __init__(self,url:str)->None:self.url=url


class FakeRoute:
    def __init__(self,url:str)->None:self.url=url; self.abort_calls:list[str]=[]; self.continue_calls:list[str]=[]
    async def abort(self)->None:self.abort_calls.append(self.url)
    async def continue_(self)->None:self.continue_calls.append(self.url)


class FakePage:
    def __init__(self,html:str="",error:BaseException|None=None,screenshot_bytes:bytes=b"",
                 redirect_url:str|None=None,content_error:BaseException|None=None)->None:
        self.html=html; self.error=error; self.screenshot_bytes=screenshot_bytes; self.redirect_url=redirect_url
        self.content_error=content_error
        self.goto_calls:list[dict[str,object]]=[]; self.screenshot_calls:list[dict[str,object]]=[]; self.closed=False
        self.route_handlers:dict[str,Any]={}; self.requests:list[FakeRoute]=[]
    async def route(self,pattern:str,handler:Any)->None:self.route_handlers[pattern]=handler
    async def simulate_request(self,url:str)->FakeRoute:
        """模拟浏览器发起一次请求（如跟随重定向），走注册的 route handler 并返回路由结果。"""
        route=FakeRoute(url); self.requests.append(route)
        handler=next(iter(self.route_handlers.values()),None)
        if handler is not None:await handler(route,FakeRequest(url))
        return route
    async def goto(self,url:str,**options:object)->None:
        self.goto_calls.append({"url":url,**options})
        if self.error is not None:raise self.error
        if self.redirect_url:
            # 模拟 302：浏览器对重定向目标再发一次请求，被 abort 时整个导航失败。
            route=await self.simulate_request(self.redirect_url)
            if route.abort_calls:raise RuntimeError(f"net::ERR_ABORTED at {self.redirect_url}")
    async def content(self)->str:
        if self.content_error is not None:raise self.content_error
        return self.html
    async def screenshot(self,**options:object)->bytes:
        self.screenshot_calls.append(options)
        return self.screenshot_bytes
    async def close(self)->None:self.closed=True


class FakeContext:
    def __init__(self,page:FakePage)->None:self.page=page; self.closed=False
    async def new_page(self)->FakePage:return self.page
    async def close(self)->None:self.closed=True


class FakeBrowser:
    def __init__(self,context:FakeContext)->None:self.context=context; self.closed=False; self.launch_options:dict[str,object]={}
    async def new_context(self,**options:object)->FakeContext:self.context_options=options; return self.context
    async def close(self)->None:self.closed=True


class FakeChromium:
    def __init__(self,browser:FakeBrowser)->None:self.browser=browser
    async def launch(self,**options:object)->FakeBrowser:self.browser.launch_options=options; return self.browser


class FakePlaywright:
    def __init__(self,chromium:FakeChromium)->None:self.chromium=chromium; self.stopped=False
    async def stop(self)->None:self.stopped=True


class FakePlaywrightFactory:
    def __init__(self,page:FakePage)->None:
        context=FakeContext(page); browser=FakeBrowser(context)
        self.instance=FakePlaywright(FakeChromium(browser)); self.browser=browser; self.context=context; self.page=page
    async def __call__(self)->FakePlaywright:return self.instance


class MissingPlaywrightFactory:
    async def __call__(self)->None:
        raise ImportError("No module named 'playwright'")


class PlaywrightClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_parses_page_and_reuses_resources_until_close(self):
        page=FakePage((Path(__file__).parent / "fixtures" / "bing_search.html").read_text(encoding="utf-8"))
        factory=FakePlaywrightFactory(page); client=PlaywrightSearchClient(None,headless=False,playwright_factory=factory)
        response=await client.search("人工智能",engine="bing",freshness="day",timeout_seconds=12,max_results=2)
        self.assertEqual(response.provider_type,"playwright"); self.assertEqual(response.model,"bing")
        self.assertTrue(response.cited); self.assertEqual(len(response.results),2)
        self.assertEqual(page.goto_calls[0]["url"],PlaywrightSearchClient.search_url("bing","人工智能","day"))
        self.assertEqual(page.goto_calls[0]["timeout"],12000)
        self.assertEqual(factory.browser.launch_options["headless"],False)
        self.assertTrue(page.closed)
        await client.close()
        self.assertTrue(factory.context.closed); self.assertTrue(factory.browser.closed); self.assertTrue(factory.instance.stopped)

    async def test_missing_dependency_has_explicit_error(self):
        client=PlaywrightSearchClient(None,playwright_factory=MissingPlaywrightFactory())
        with self.assertRaises(SearchBackendError) as caught:
            await client.search("人工智能",engine="bing",freshness="any",timeout_seconds=12,max_results=2)
        self.assertEqual(caught.exception.error_class,"playwright_unavailable")

    async def test_navigation_timeout_is_structured_network_error(self):
        page=FakePage("",TimeoutError("Timeout 12000ms exceeded"))
        client=PlaywrightSearchClient(None,playwright_factory=FakePlaywrightFactory(page))
        with self.assertRaises(SearchBackendError) as caught:
            await client.search("人工智能",engine="bing",freshness="any",timeout_seconds=12,max_results=2)
        self.assertEqual(caught.exception.error_class,"network")

    async def test_search_registers_route_guard_before_goto(self):
        # 与 screenshot 对齐：search() 每一跳（含重定向）都必须过公网校验。
        page=FakePage((Path(__file__).parent / "fixtures" / "bing_search.html").read_text(encoding="utf-8"))
        client=PlaywrightSearchClient(None,playwright_factory=FakePlaywrightFactory(page))
        response=await client.search("人工智能",engine="bing",freshness="day",timeout_seconds=12,max_results=2)
        self.assertEqual(len(response.results),2)
        self.assertIn("**/*",page.route_handlers)  # goto 前已注册逐跳校验
        route=await page.simulate_request("http://8.8.8.8/static/app.js")
        self.assertEqual(route.continue_calls,["http://8.8.8.8/static/app.js"])
        self.assertEqual(route.abort_calls,[])

    async def test_search_redirect_to_internal_address_is_blocked(self):
        # 公网搜索结果页被 302 到云元数据地址：重定向目标必须被 abort，不得进入解析。
        page=FakePage((Path(__file__).parent / "fixtures" / "bing_search.html").read_text(encoding="utf-8"),
                      redirect_url="http://169.254.169.254/latest/meta-data/")
        client=PlaywrightSearchClient(None,playwright_factory=FakePlaywrightFactory(page))
        with self.assertRaises(SearchBackendError) as caught:
            await client.search("人工智能",engine="bing",freshness="any",timeout_seconds=12,max_results=2)
        self.assertEqual(caught.exception.error_class,"browser_error")
        self.assertEqual(len(page.requests),1)
        self.assertEqual(page.requests[0].abort_calls,["http://169.254.169.254/latest/meta-data/"])
        self.assertEqual(page.requests[0].continue_calls,[])

    async def test_content_timeout_is_structured_network_error(self):
        # page.content() 与 goto 同源超时：卡住不得无限期挂起，按网络错误分类。
        page=FakePage("",content_error=TimeoutError("Timeout 12000ms exceeded"))
        client=PlaywrightSearchClient(None,playwright_factory=FakePlaywrightFactory(page))
        with self.assertRaises(SearchBackendError) as caught:
            await client.search("人工智能",engine="bing",freshness="any",timeout_seconds=12,max_results=2)
        self.assertEqual(caught.exception.error_class,"network")
        self.assertTrue(page.closed)

    async def test_content_failure_is_browser_error(self):
        page=FakePage("",content_error=RuntimeError("Execution context was destroyed"))
        client=PlaywrightSearchClient(None,playwright_factory=FakePlaywrightFactory(page))
        with self.assertRaises(SearchBackendError) as caught:
            await client.search("人工智能",engine="bing",freshness="any",timeout_seconds=12,max_results=2)
        self.assertEqual(caught.exception.error_class,"browser_error")

    async def test_engine_case_is_normalized_for_url_and_parser(self):
        # "Bing" 必须与 search_url 走同一套归一化选 Bing 解析器，否则 Bing 页面被 DuckDuckGo 解析器解析成空结果。
        page=FakePage((Path(__file__).parent / "fixtures" / "bing_search.html").read_text(encoding="utf-8"))
        client=PlaywrightSearchClient(None,playwright_factory=FakePlaywrightFactory(page))
        response=await client.search("人工智能",engine="Bing",freshness="day",timeout_seconds=12,max_results=2)
        self.assertEqual(page.goto_calls[0]["url"],PlaywrightSearchClient.search_url("bing","人工智能","day"))
        self.assertEqual(len(response.results),2)
        self.assertEqual(response.model,"Bing")

    async def test_duckduckgo_engine_case_selects_duckduckgo_parser(self):
        page=FakePage((Path(__file__).parent / "fixtures" / "duckduckgo_search.html").read_text(encoding="utf-8"))
        client=PlaywrightSearchClient(None,playwright_factory=FakePlaywrightFactory(page))
        response=await client.search("人工智能",engine=" DuckDuckGo ",freshness="week",timeout_seconds=12,max_results=5)
        self.assertEqual(page.goto_calls[0]["url"],PlaywrightSearchClient.search_url("duckduckgo","人工智能","week"))
        self.assertEqual(len(response.results),3)

    async def test_search_fills_weak_flag_from_query(self):
        # 1 字 CJK 查询无法命中 2 字滑窗，百科词条也判弱（单字命中不足以判定相关）。
        page=FakePage(_bing_html([("猫（猫科猫属动物）_百度百科","https://baike.baidu.com/item/%E7%8C%AB/1364",
                                   "2023年10月25日 · 猫具有19对染色体，寿命有12-17年。")]))
        client=PlaywrightSearchClient(None,playwright_factory=FakePlaywrightFactory(page))
        response=await client.search("猫",engine="bing",freshness="any",timeout_seconds=12,max_results=5)
        self.assertEqual(len(response.results),1)
        self.assertTrue(response.results[0].weak)

    async def test_blocked_page_is_not_treated_as_empty_result(self):
        page=FakePage('<html><body><div class="b_captcha">验证码</div></body></html>')
        client=PlaywrightSearchClient(None,playwright_factory=FakePlaywrightFactory(page))
        with self.assertRaises(SearchBackendError) as caught:
            await client.search("人工智能",engine="bing",freshness="any",timeout_seconds=12,max_results=2)
        self.assertEqual(caught.exception.error_class,"blocked")

    async def test_screenshot_returns_png_bytes(self):
        png=b"\x89PNG\r\n\x1a\nfake-screenshot"
        page=FakePage(screenshot_bytes=png)
        client=PlaywrightSearchClient(None,playwright_factory=FakePlaywrightFactory(page))
        result=await client.screenshot("http://8.8.8.8/page",12)
        self.assertEqual(result,png)
        self.assertEqual(page.goto_calls[0]["url"],"http://8.8.8.8/page")
        self.assertEqual(page.goto_calls[0]["timeout"],12000)
        self.assertEqual(page.screenshot_calls[0]["type"],"png")
        self.assertTrue(page.closed)

    async def test_screenshot_rejects_private_addresses(self):
        client=PlaywrightSearchClient(None,playwright_factory=FakePlaywrightFactory(FakePage()))
        for url in ("http://127.0.0.1/x","http://localhost","http://192.168.1.1","ftp://x"):
            with self.subTest(url=url):
                with self.assertRaises(SearchBackendError) as caught:
                    await client.screenshot(url,12)
                self.assertEqual(caught.exception.error_class,"invalid_response")

    async def test_screenshot_route_handler_allows_public_requests(self):
        png=b"\x89PNG\r\n\x1a\nfake-screenshot"
        page=FakePage(screenshot_bytes=png)
        client=PlaywrightSearchClient(None,playwright_factory=FakePlaywrightFactory(page))
        result=await client.screenshot("http://8.8.8.8/page",12)
        self.assertEqual(result,png)
        self.assertIn("**/*",page.route_handlers)  # goto 前已注册逐跳校验
        route=await page.simulate_request("http://8.8.8.8/static/app.js")
        self.assertEqual(route.continue_calls,["http://8.8.8.8/static/app.js"])
        self.assertEqual(route.abort_calls,[])

    async def test_screenshot_redirect_to_internal_address_is_blocked(self):
        # 公网 URL 被 302 到云元数据地址：第二次请求必须被 abort，截图不得发生。
        page=FakePage(redirect_url="http://169.254.169.254/latest/meta-data/")
        client=PlaywrightSearchClient(None,playwright_factory=FakePlaywrightFactory(page))
        with self.assertRaises(SearchBackendError) as caught:
            await client.screenshot("http://8.8.8.8/page",12)
        self.assertEqual(caught.exception.error_class,"browser_error")
        self.assertEqual(page.goto_calls[0]["url"],"http://8.8.8.8/page")
        self.assertEqual(page.screenshot_calls,[])  # 内网内容不会进入截图返回值
        self.assertEqual(len(page.requests),1)
        self.assertEqual(page.requests[0].abort_calls,["http://169.254.169.254/latest/meta-data/"])
        self.assertEqual(page.requests[0].continue_calls,[])

    async def test_screenshot_timeout_maps_to_network(self):
        page=FakePage("",TimeoutError("Timeout 12000ms exceeded"))
        client=PlaywrightSearchClient(None,playwright_factory=FakePlaywrightFactory(page))
        with self.assertRaises(SearchBackendError) as caught:
            await client.screenshot("http://8.8.8.8/page",12)
        self.assertEqual(caught.exception.error_class,"network")


class SearchServiceBrowseScreenshotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self)->None:
        self.tmp=tempfile.TemporaryDirectory(); self.store=LifeStore(self.tmp.name); await self.store.initialize()

    async def asyncTearDown(self)->None:
        await self.store.close(); self.tmp.cleanup()

    async def test_browse_screenshot_delegates_to_playwright_client(self):
        class FakeClient:
            def __init__(self)->None:self.url=""; self.timeout=0.0
            async def screenshot(self,url:str,timeout_seconds:float)->bytes:
                self.url=url; self.timeout=timeout_seconds
                return b"png"
        client=FakeClient()
        service=SearchService(MaiLifeSettings(),HttpClientDummy(),self.store,DummyLogger(),playwright_client=client)
        result=await service.browse_screenshot("http://8.8.8.8/x")
        self.assertEqual(result,b"png")
        self.assertEqual(client.url,"http://8.8.8.8/x")
        self.assertEqual(client.timeout,12.0)

    async def test_browse_screenshot_without_playwright_raises_service_not_configured(self):
        config=MaiLifeSettings()
        config.search_api.providers=[SearchProviderProfile(enabled=True,provider_type="bocha",api_keys=["k"])]
        service=SearchService(config,HttpClientDummy(),self.store,DummyLogger())
        with self.assertRaises(SearchBackendError) as caught:
            await service.browse_screenshot("http://8.8.8.8/x")
        # 未启用 Playwright 服务是配置问题，与“未装 Chromium”环境问题区分（P3-20）。
        self.assertEqual(caught.exception.error_class,"service_not_configured")


def _bing_html(results:list[tuple[str,str,str]])->str:
    """按 (标题, 链接, 摘要) 生成 Bing 自然结果卡片 HTML，供 weak 判定测试复用。"""
    cards="".join(
        f'<li class="b_algo"><h2><a href="{url}">{title}</a></h2><div class="b_caption"><p>{snippet}</p></div></li>'
        for title,url,snippet in results)
    return f'<!doctype html><html><body><ol id="b_results">{cards}</ol></body></html>'


def _bing_q(url:str)->str:
    return parse_qs(urlsplit(url).query)["q"][0]


class WeakResultDetectionTests(unittest.TestCase):
    """weak 判定的正反例全部来自 2026-10-17 cn.bing.com 实测结果（探针原样摘录）。"""

    def test_single_char_query_stays_weak(self):
        # 1 字 CJK 查询：滑窗最小 2 字，单字无法命中，字面单字不足以判定相关
        # （实证：'猫'只命中百科词条）；单字 ASCII 不构成词元，同样无法证明相关。
        self.assertTrue(is_weak_result("猫","猫（猫科猫属动物）_百度百科",
            "2023年10月25日 · 猫具有19对染色体，寿命有12-17年，繁殖与生育期长达一生。"))
        self.assertTrue(is_weak_result("x","无关的结果标题","毫不相干的摘要内容，没有任何词元重叠。"))

    def test_short_query_normal_hits_are_not_weak(self):
        # 2 字查询完整字面命中是强相关信号，不得因查询短而一律判弱（评审探针实测的正常结果）。
        for query,title,snippet in (
            ("天气","北京天气预报","北京 多云 32℃/22℃，8 小时之前更新。"),
            ("新闻","新闻中心_腾讯网","今日要闻、财经、科技、国际新闻实时更新。"),
            ("电影","电影_豆瓣","提供最新的电影介绍、影评及评分。"),
            ("音乐","音乐_豆瓣音乐","豆瓣音乐：发现让人愉悦的好音乐。"),
            ("go","The Go Programming Language","Go is an open source programming language."),
            ("ai","Artificial intelligence - Wikipedia","Artificial intelligence (AI) is a wide-ranging branch of computer science."),
        ):
            with self.subTest(query=query):
                self.assertFalse(is_weak_result(query,title,snippet))
        # 2 字查询与结果毫无重叠时仍然判弱。
        self.assertTrue(is_weak_result("天气","Minecraft 论坛","游戏 MOD 讨论与安装教程。"))

    def test_partial_token_hit_and_gibberish_are_weak(self):
        # '最近的中文开源模型发布' 实测只命中"最近"，返回歌曲和热榜。
        self.assertTrue(is_weak_result("最近的中文开源模型发布",
            "最近（李圣杰2006年演唱的歌曲，收录于专辑《关于你的歌 ...",
            "2006年6月16日 · 《最近》是由李圣杰演唱的华语流行歌曲，于2006年6月16日发行。"))
        self.assertTrue(is_weak_result("最近的中文开源模型发布","今日热榜",
            "2026年9月22日 · 54 万热度 41 最近密集收到各种 AI 逆向游戏、软件的新闻和资讯。"))
        # 乱词查询实测返回毫不相干的百科/影视结果。
        self.assertTrue(is_weak_result("乱词查询 xzqvj wkbm","乱（汉语汉字）_百度百科",
            "2023年12月7日 · 此字始见于西周时期金文。 本义是理丝和丝乱，后引申为治理、混乱。"))

    def test_weather_sites_are_not_weak(self):
        # '明天天气怎么样' 实测返回 5 个天气站点，均不得误判为弱相关。
        for title,snippet in (
            ("天气预报-中国天气网","天气预报-中国天气网. 预警. 信息. 地图. 卫星. 全国. 北京. 上海. 天津."),
            ("上海天气预报,上海7天天气预报,上海15天天气预报,上海天气查询","8 小时之前 · 蓝天预报综合天气现象、能见度、空气质量等因子。"),
            ("国内天气预报","长沙市 多云转小雨 32℃ / 22℃ 成都 多云转小雨 23℃ / 15℃。"),
        ):
            with self.subTest(title=title):
                self.assertFalse(is_weak_result("明天天气怎么样",title,snippet))

    def test_open_source_sites_are_not_weak(self):
        # '开源软件' 实测返回 OSCHINA/GitHub/Gitee，均不得误判为弱相关。
        for title,snippet in (
            ("开源软件 - OSCHINA - 中文开源技术交流社区","1 天前 · yovoice 是一个开源的本地 AI 音频与音乐工作室。"),
            ("GitHub - GitHubDaily/GitHubDaily: 坚持分享 GitHub 上高质量、有 …","秉着挖掘开源价值的初衷，GitHubDaily 自 2015 年 10 月 10 日正式成立。"),
            ("开源中国 - 开源中国 - Gitee","自 2013 年上线以来，Gitee 共服务了 1200 万开发者用户，是国内首屈一指的开源软件技术交流平台。"),
        ):
            with self.subTest(title=title):
                self.assertFalse(is_weak_result("开源软件",title,snippet))

    def test_search_result_weak_defaults_false(self):
        self.assertFalse(SearchResult("标题","https://example.com","摘要").weak)
        self.assertTrue(SearchResult("标题","https://example.com","摘要",weak=True).weak)


class WeakParserTests(unittest.TestCase):
    def test_bing_parser_marks_unrelated_results_weak(self):
        # 查询与结果无任何词元重叠时判弱（2 字查询命中百科词条不再判弱，改由 WeakResultDetectionTests 覆盖）。
        html=_bing_html([("独立（汉语词语）_百度百科","https://baike.baidu.com/item/%E7%8B%AC%E7%AB%8B/3259",
                          "独立（拼音：dú lì），汉语词语，指不依附他者的存在状态。"),
                         ("独立的意思_独立的解释-汉语国学","https://www.hanyuguoxue.com/cidian/ci-ec8adeb3a",
                          "独立的拼音是：dú lì，名词、动词、形容词。")])
        results=BingSearchParser().parse(html,5,"量子计算")
        self.assertEqual(len(results),2)
        self.assertTrue(all(item.weak for item in results))

    def test_bing_parser_keeps_weather_results_not_weak(self):
        html=_bing_html([("天气预报-中国天气网","https://www.weather.com.cn/forecast/index_cy.shtml",
                          "天气预报-中国天气网. 预警. 信息. 地图. 卫星. 全国. 北京."),
                         ("国内天气预报","https://www.nmc.cn/publish/forecast","长沙市 多云转小雨 32℃ / 22℃。")])
        results=BingSearchParser().parse(html,5,"明天天气怎么样")
        self.assertEqual(len(results),2)
        self.assertTrue(all(not item.weak for item in results))

    def test_parser_without_query_leaves_weak_false(self):
        results=BingSearchParser().parse((Path(__file__).parent / "fixtures" / "bing_search.html").read_text(encoding="utf-8"),5)
        self.assertTrue(all(not item.weak for item in results))


if __name__=="__main__":unittest.main()
