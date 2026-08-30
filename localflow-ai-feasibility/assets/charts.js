(function () {
  var style = getComputedStyle(document.documentElement);
  var accent = style.getPropertyValue('--accent').trim();
  var accent2 = style.getPropertyValue('--accent2').trim();
  var ink = style.getPropertyValue('--ink').trim();
  var muted = style.getPropertyValue('--muted').trim();
  var rule = style.getPropertyValue('--rule').trim();
  var bg2 = style.getPropertyValue('--bg2').trim();

  var tooltipBase = { appendToBody: true, backgroundColor: 'rgba(255,255,255,.96)', borderColor: rule, textStyle: { color: ink } };

  // ----- 图 1：模块工作量 -----
  var effortEl = document.getElementById('chart-effort');
  if (effortEl) {
    var chart = echarts.init(effortEl, null, { renderer: 'svg' });
    chart.setOption({
      animation: false,
      tooltip: Object.assign({}, tooltipBase, { axisPointer: { type: 'shadow' } }),
      grid: { left: 8, right: 20, top: 20, bottom: 8, containLabel: true },
      xAxis: { type: 'value', axisLabel: { color: muted, fontSize: 11 }, splitLine: { lineStyle: { color: rule } } },
      yAxis: {
        type: 'category', inverse: true,
        data: ['Electron 打包','LangGraph','微内核','事件回溯','L1 工具缓存','L2 子任务缓存','L3 KV 缓存','L4 语义缓存','SubAgent 并行','本地模型全自动管理','双后端融合','多API聚合','难度路由','风控熔断'],
        axisLabel: { color: ink, fontSize: 11 }, axisLine: { lineStyle: { color: rule } }, axisTick: { show: false }
      },
      series: [{
        name: '工作量(周)', type: 'bar', barWidth: '62%',
        itemStyle: {
          borderRadius: [0, 5, 5, 0],
          color: function (p) {
            var v = p.value;
            return v >= 6 ? accent2 : (v >= 4 ? accent : '#a7c0f8');
          }
        },
        label: { show: true, position: 'right', color: ink, fontSize: 11, formatter: '{c}' },
        data: [1.5, 1.5, 2.5, 2, 1.5, 2, 2.5, 5, 3.5, 10, 5, 6.5, 5, 2.5]
      }]
    });
    window.addEventListener('resize', function () { chart.resize(); });
  }

  // ----- 图 2：风险雷达 -----
  var riskEl = document.getElementById('chart-risk');
  if (riskEl) {
    var chart2 = echarts.init(riskEl, null, { renderer: 'svg' });
    chart2.setOption({
      animation: false,
      tooltip: Object.assign({}, tooltipBase, { trigger: 'item' }),
      legend: { bottom: 0, textStyle: { color: ink, fontSize: 11 } },
      radar: {
        indicator: [
          { name: '技术不确定性', max: 10 },
          { name: '工程复杂度', max: 10 },
          { name: '长期维护成本', max: 10 },
          { name: '指标兑现风险', max: 10 },
          { name: '生态/依赖风险', max: 10 }
        ],
        radius: '62%', center: ['50%', '46%'],
        axisName: { color: muted, fontSize: 12 },
        splitLine: { lineStyle: { color: rule } },
        splitArea: { areaStyle: { color: [bg2, 'rgba(226,233,244,.35)'] } },
        axisLine: { lineStyle: { color: rule } }
      },
      series: [{
        type: 'radar',
        data: [
          { name: 'MVP（收窄后）', value: [3, 4, 5, 4, 4], itemStyle: { color: accent }, areaStyle: { color: accent + '66' } },
          { name: '进阶版（完整规划）', value: [9, 9, 9, 8, 7], itemStyle: { color: accent2 }, areaStyle: { color: accent2 + '55' } }
        ]
      }]
    });
    window.addEventListener('resize', function () { chart2.resize(); });
  }
})();