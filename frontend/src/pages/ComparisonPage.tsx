/**
 * @fileoverview 竞品对比分析页面
 *
 * 职责：
 * - 对比瑞幸与星巴克在门店数量、城市覆盖、城市层级渗透上的差异
 * - 展示共址率（瑞幸门店在星巴克 200m 内的比例）和平均距离
 * - 通过城市层级柱状图展现两个品牌在不同层级城市的下沉策略差异
 *
 * 核心洞察：
 * - 瑞幸在二三线城市的门店占比显著高于星巴克（下沉市场策略）
 * - 共址率反映"贴身战术"，相当比例的瑞幸门店开在星巴克附近
 */

import React, { useState, useMemo } from 'react';
import { Card, Spin, Row, Col, Statistic, Select, Table, Alert } from 'antd';
import ReactECharts from 'echarts-for-react';
import { useComparison } from '../hooks/useComparison';
import { useCities } from '../hooks/useCities';

/**
 * 竞品对比分析页面
 *
 * 对比瑞幸与星巴克在门店分布、城市渗透和选址策略上的关键差异。
 */
const ComparisonPage: React.FC = () => {
  /** 城市筛选（空 = 全国） */
  const [city, setCity] = useState<string>('');

  const { comparison, cityTierStats, loading, error } = useComparison(city || undefined);
  const { cityOptions, loading: citiesLoading } = useCities();

  /** 瑞幸门店数（从 brand_counts 中提取） */
  const luckinCount = useMemo(
    () => comparison?.brand_counts?.find((b) => b.brand === 'luckin')?.count || 0,
    [comparison],
  );
  /** 星巴克门店数（从 brand_counts 中提取） */
  const sbCount = useMemo(
    () => comparison?.brand_counts?.find((b) => b.brand === 'starbucks')?.count || 0,
    [comparison],
  );

  /**
   * 城市层级渗透对比柱状图
   * useMemo 缓存：仅在 cityTierStats 变化时重建
   *
   * 配置说明：
   * - xAxis: 4 个城市层级（一线/新一线/二线/其他）
   * - yAxis: 各层级门店总数
   * - 双系列分组柱状图：瑞幸蓝色 vs 星巴克绿色
   */
  const tierOption = useMemo(() => {
    if (!cityTierStats.length) return {};
    return {
      tooltip: { trigger: 'axis' as const },
      legend: { data: ['瑞幸', '星巴克'] },
      xAxis: { type: 'category' as const, data: ['一线', '新一线', '二线', '其他'] },
      yAxis: { type: 'value' as const, name: '门店数' },
      series: ['luckin', 'starbucks'].map((brand) => ({
        name: brand === 'luckin' ? '瑞幸' : '星巴克',
        type: 'bar' as const,
        data: [1, 2, 3, 4].map((tier) => {
          const match = cityTierStats.find((s) => s.tier === tier && s.brand === brand);
          return match?.total_stores || 0;
        }),
        itemStyle: { color: brand === 'luckin' ? '#1677ff' : '#52c41a' },
      })),
    };
  }, [cityTierStats]);

  // 首次加载：数据完全为空时显示全屏 Spin
  if (!comparison && (loading || citiesLoading)) {
    return <div style={{ textAlign: 'center', padding: 100 }}><Spin size="large" tip="加载中..." /></div>;
  }

  return (
    <div style={{ padding: 24 }}>
      {error && <Alert message={`数据异常: ${error}`} type="warning" showIcon style={{ marginBottom: 16 }} closable />}

      {/* 城市筛选器 */}
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col xs={24} sm={8}>
          <Select
            style={{ width: '100%' }}
            placeholder="选择城市（全部）"
            allowClear
            value={city || undefined}
            onChange={(val) => setCity(val || '')}
            options={cityOptions}
            showSearch
            filterOption={(input, option) =>
              (option?.label as string).toLowerCase().includes(input.toLowerCase())
            }
          />
        </Col>
      </Row>

      {/* KPI 卡片行 */}
      <Row gutter={[16, 16]}>
        <Col xs={12} sm={8}>
          <Card>
            <Statistic title="瑞幸门店" value={luckinCount} valueStyle={{ color: '#1677ff' }} />
          </Card>
        </Col>
        <Col xs={12} sm={8}>
          <Card>
            <Statistic title="星巴克门店" value={sbCount} valueStyle={{ color: '#52c41a' }} />
          </Card>
        </Col>
        <Col xs={24} sm={8}>
          <Card>
            <Statistic
              title="共址率(200m内)"
              value={comparison?.co_location?.co_location_rate || 0}
              suffix="%"
              valueStyle={{ color: '#fa8c16' }}
            />
          </Card>
        </Col>
      </Row>

      {/* 图表 + 指标表格 */}
      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} lg={12}>
          <Card title="城市层级渗透对比">
            <ReactECharts option={tierOption} style={{ height: 350 }} />
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card title="关键指标对比">
            <Table
              dataSource={[
                { key: 'total', metric: '门店总数', luckin: luckinCount, starbucks: sbCount },
                {
                  key: 'co_loc',
                  metric: '与竞品200m内共址',
                  luckin: `${comparison?.co_location?.co_location_rate || 0}%`,
                  starbucks: '-',
                },
                {
                  key: 'distance',
                  metric: '到最近星巴克平均距离',
                  luckin: comparison?.avg_nearest_starbucks_m
                    ? `${Math.round(comparison.avg_nearest_starbucks_m)}m`
                    : '-',
                  starbucks: '-',
                },
              ]}
              columns={[
                { title: '指标', dataIndex: 'metric', key: 'metric' },
                { title: '瑞幸', dataIndex: 'luckin', key: 'luckin' },
                { title: '星巴克', dataIndex: 'starbucks', key: 'starbucks' },
              ]}
              pagination={false}
              size="small"
            />
          </Card>
        </Col>
      </Row>

      {/* 策略解读：提供业务层面的定性分析 */}
      <Row style={{ marginTop: 16 }}>
        <Col span={24}>
          <Card title="选址策略差异解读">
            <ul>
              <li><strong>城市下沉</strong>：瑞幸在二三线城市的门店占比显著高于星巴克，体现"下沉市场"策略</li>
              <li><strong>快取店模式</strong>：瑞幸以小型快取店为主（~70%），选址更灵活，对商场/写字楼大堂等小面积空间利用更充分</li>
              <li><strong>贴身战术</strong>：相当比例的瑞幸门店开在星巴克200m范围内，体现"追随+差异化"策略</li>
              <li><strong>数字化驱动</strong>：瑞幸依靠App下单+外卖配送，对门店可见度和临街面要求低于星巴克</li>
            </ul>
          </Card>
        </Col>
      </Row>
    </div>
  );
};

export default ComparisonPage;
