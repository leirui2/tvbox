/**
 * TVBox drpy 爬虫模板 —— 抓「普通影视网站」（非 MacCMS，需解析 HTML）
 *
 * 这是真正考验爬虫能力的情况：目标站没有标准接口，
 * 列表、详情、播放地址都要从 HTML 里用正则/选择器抠出来。
 *
 * 关键技巧：
 *   - 用 pdfa / pdfh 等内置函数做 HTML 抽取（drpy 内置了 cheerio 风格的解析）
 *   - 先用浏览器 F12 看目标站的 HTML 结构，再写对应的抽取规则
 */

var rule = {
    title: '示例影视站',
    host: 'https://example2.com',

    class_name: '电影&电视剧&动漫',
    class_url: 'dianying&dianshiju&dongman',

    // 列表页，fypage = 页码
    url: '/list/dianying/page/fypage.html',
    searchUrl: '/search/wd.html',
    searchable: 1,
    quickSearch: 1,

    // ========== 列表页解析：从 HTML 抠出视频条目 ==========
    // 假设列表页 HTML 结构是：
    //   <div class="item">
    //     <a href="/play/123.html"><img src="封面"><span class="title">片名</span></a>
    //   </div>
    list_rule: 'pdfa&&jq',
    list: `js:
        var $ = cheerio.load(input);       // input = 列表页 HTML
        var items = [];
        $('.item').each(function() {
            var a = $(this).find('a').first();
            items.push({
                vod_id: a.attr('href').replace('/play/', '').replace('.html', ''),
                vod_name: $(this).find('.title').text().trim(),
                vod_pic: $(this).find('img').attr('src'),
                vod_remarks: $(this).find('.note').text().trim()  // 如「更新至12集」
            });
        });
        return items;
    `,

    // ========== 详情页解析：提取播放地址 ==========
    // 假设详情页 HTML 结构是：
    //   <script> var player_aaaa = {"url":"https://...m3u8"}; </script>
    lazy: `js:
        var $ = cheerio.load(input);       // input = 详情页 HTML
        // 方式1：从 script 里正则抠出 m3u8 地址
        var m = input.match(/player_aaaa[\s\S]*?"url":"([^"]+)"/);
        if (m) return [{ name: '高清', url: m[1] }];
        // 方式2：从 <video> 标签的 src 抠
        var src = $('video').attr('src');
        if (src) return [{ name: '默认', url: src }];
        return [];
    `
};
