import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '../ui/card';
import { StateMessage } from '../ui/StateMessage';
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';
import type { SentimentDataPoint } from '../../types';

interface SentimentAnalysisProps {
    data: SentimentDataPoint[];
}

export function SentimentAnalysis({ data }: SentimentAnalysisProps) {
    const latestPoint = data[data.length - 1];

    const CustomTooltip = ({ active, payload }: any) => {
        if (active && payload && payload.length) {
            return (
                <div className="bg-white border border-gray-200 rounded-lg p-3 shadow-sm">
                    <p className="text-sm font-medium text-gray-900 mb-2">{payload[0].payload.date}</p>
                    {payload.map((entry: any, index: number) => (
                        <p key={index} className="text-sm" style={{ color: entry.color }}>
                            {entry.name}: {entry.value}%
                        </p>
                    ))}
                </div>
            );
        }
        return null;
    };

    return (
        <Card className="h-full max-w-full overflow-hidden border-gray-200 bg-white shadow-sm">
            <CardHeader className="p-4 sm:p-5 pb-2">
                <CardTitle className="text-base font-semibold text-gray-900">Sentiment Signals</CardTitle>
                <CardDescription className="text-xs text-gray-500">Expert and public sentiment as supporting context</CardDescription>
            </CardHeader>
            <CardContent className="p-4 sm:p-5 pt-2">
                {data.length > 0 ? (
                    <>
                        <div className="h-[170px] sm:h-[190px] w-full overflow-hidden">
                        <ResponsiveContainer width="100%" height="100%">
                            <AreaChart data={data} margin={{ top: 14, right: 5, left: 0, bottom: 5 }}>
                                <defs>
                                    <linearGradient id="expertGradient" x1="0" y1="0" x2="0" y2="1">
                                        <stop offset="5%" stopColor="#14b8a6" stopOpacity={0.2} />
                                        <stop offset="95%" stopColor="#14b8a6" stopOpacity={0} />
                                    </linearGradient>
                                    <linearGradient id="publicGradient" x1="0" y1="0" x2="0" y2="1">
                                        <stop offset="5%" stopColor="#a855f7" stopOpacity={0.1} />
                                        <stop offset="95%" stopColor="#a855f7" stopOpacity={0} />
                                    </linearGradient>
                                </defs>
                                <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" vertical={false} />
                                <XAxis
                                    dataKey="date"
                                    tick={{ fontSize: 10, fill: '#9ca3af' }}
                                    axisLine={false}
                                    tickLine={false}
                                    dy={10}
                                />
                                <YAxis
                                    domain={[0, 100]}
                                    tick={{ fontSize: 10, fill: '#9ca3af' }}
                                    axisLine={false}
                                    tickLine={false}
                                    dx={-10}
                                />
                                <Tooltip content={<CustomTooltip />} cursor={{ stroke: '#cbd5e1', strokeWidth: 1, strokeDasharray: '4 4' }} />
                                <Legend iconType="circle" wrapperStyle={{ fontSize: '12px', paddingTop: '16px' }} />
                                <Area
                                    type="monotone"
                                    dataKey="expertSentiment"
                                    stroke="#0d9488"
                                    strokeWidth={2}
                                    fill="url(#expertGradient)"
                                    name="Expert Sentiment"
                                />
                                <Area
                                    type="monotone"
                                    dataKey="publicSentiment"
                                    stroke="#9333ea"
                                    strokeWidth={2}
                                    fill="url(#publicGradient)"
                                    name="Public Sentiment"
                                />
                            </AreaChart>
                        </ResponsiveContainer>
                        </div>
                        <div className="mt-4 pt-3 border-t border-gray-50 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
                            <p className="text-xs text-gray-500 leading-relaxed break-words">
                                Sentiment is supporting context; use it alongside the forecast result.
                            </p>
                            <div className="flex shrink-0 gap-4">
                                <div className="text-left sm:text-right">
                                    <p className="text-[10px] text-gray-400 uppercase tracking-wider font-semibold">Expert</p>
                                    <span className="text-lg font-bold text-anizai-teal-600">{latestPoint?.expertSentiment}%</span>
                                </div>
                                <div className="text-left sm:text-right">
                                    <p className="text-[10px] text-gray-400 uppercase tracking-wider font-semibold">Public</p>
                                    <span className="text-lg font-bold text-anizai-purple-600">{latestPoint?.publicSentiment}%</span>
                                </div>
                            </div>
                        </div>
                    </>
                ) : (
                    <StateMessage
                        compact
                        title="No sentiment data"
                        description="Expert and public sentiment trends are not available for this forecast."
                    />
                )}
            </CardContent>
        </Card>
    );
}
