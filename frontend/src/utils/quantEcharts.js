import { init, use } from 'echarts/core'
import { LineChart, BarChart, CandlestickChart, HeatmapChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent, DataZoomComponent, VisualMapComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
use([LineChart, BarChart, CandlestickChart, HeatmapChart, GridComponent, LegendComponent, TooltipComponent, DataZoomComponent, VisualMapComponent, CanvasRenderer])
export const mountChart = element => init(element, null, { renderer: 'canvas' })
