/**
 * @fileoverview 时间轴滑块组件
 *
 * 职责：
 * - 提供一个带年份标记的滑动条，用于按年份筛选数据（如门店分布、增长趋势）
 * - 在 min~max 范围内以 1 年为步长滑动，tooltip 显示"YYYY年"格式
 * - 每个年份节点渲染为可读的刻度标记
 *
 * 使用场景：EvolutionPage（时空演变）中用于切换不同年份的门店展示
 */

import React from 'react';
import { Slider } from 'antd';

/**
 * TimelineSlider 组件的 Props 类型
 */
interface TimelineSliderProps {
  /** 最小年份（滑块起始值） */
  min: number;
  /** 最大年份（滑块终止值） */
  max: number;
  /** 当前选中的年份 */
  value: number;
  /** 年份变更回调 */
  onChange: (year: number) => void;
}

/**
 * 时间轴滑块组件
 *
 * 基于 Ant Design Slider 封装，添加年份刻度标记和工具提示格式化。
 * 每次拖动更改触发 onChange 回调，通知父组件更新数据。
 *
 * @param props.min - 最小可选年份
 * @param props.max - 最大可选年份
 * @param props.value - 当前年份值
 * @param props.onChange - 值变更回调
 */
const TimelineSlider: React.FC<TimelineSliderProps> = ({ min, max, value, onChange }) => {
  /** 动态生成年份刻度标记：每个年份显示对应数字标签，方便快速定位 */
  const marks: Record<number, string> = {};
  for (let y = min; y <= max; y++) {
    marks[y] = String(y);
  }

  return (
    <div style={{ padding: '0 20px' }}>
      <Slider
        min={min}
        max={max}
        value={value}
        onChange={(val) => onChange(val as number)}
        marks={marks}
        step={1}
        tooltip={{ formatter: (val) => `${val}年` }}
      />
    </div>
  );
};

export default TimelineSlider;
