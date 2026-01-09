import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '../ui/card';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';

interface MarketComparisonProps {
    anizaiProbability: number;
    marketProbability: number;
}

export function MarketComparison({ anizaiProbability, marketProbability }: MarketComparisonProps) {
    const data = [
        {
            name: 'Compare',
            'Market Consensus': marketProbability,
            'Anizai Forecast': anizaiProbability,
        },
    ];

    const difference = anizaiProbability - marketProbability;
    const isBullish = difference > 0;

    // Insight-first header text
    const insightTitle = isBullish
        ? `Anizai is ${difference.toFixed(1)}% more bullish than market consensus`
        : `Anizai is ${Math.abs(difference).toFixed(1)}% more bearish than market consensus`;

    const CustomTooltip = ({ active, payload }: any) => {
        if (active && payload && payload.length) {
            return (
                <div className="bg-white border border-gray-200 rounded-lg p-3 shadow-md">
                    {payload.map((entry: any, index: number) => (
                        <div key={index} className="flex items-center gap-2 mb-1 last:mb-0">
                            <div className="w-2 h-2 rounded-full" style={{ backgroundColor: entry.color }} />
                            <p className="text-sm font-medium text-gray-700">
                                {entry.name}: <span className="text-gray-900 font-bold">{entry.value.toFixed(1)}%</span>
                            </p>
                        </div>
                    ))}
                </div>
            );
        }
        return null;
    };

    return (
        <Card className="h-full border-gray-200 bg-white shadow-sm flex flex-col">
            <CardHeader className="pb-2">
                <CardTitle className="text-base font-semibold text-gray-900 leading-tight">
                    {insightTitle}
                </CardTitle>
                <CardDescription className="text-xs text-gray-500">
                    Direct comparison against prediction market averages
                </CardDescription>
            </CardHeader>
            <CardContent className="pt-2 flex-1 flex flex-col justify-center">
                <div className="w-full h-[180px]">
                    <ResponsiveContainer width="100%" height="100%">
                        <BarChart
                            data={data}
                            layout="vertical"
                            margin={{ top: 0, right: 30, left: 0, bottom: 0 }}
                            barGap={8}
                        >
                            <CartesianGrid horizontal={false} vertical={true} stroke="#f0f0f0" strokeDasharray="3 3" />
                            <XAxis
                                type="number"
                                domain={[0, 100]}
                                hide={false}
                                tick={{ fontSize: 10, fill: '#9ca3af' }}
                                axisLine={false}
                                tickLine={false}
                                tickCount={6}
                            />
                            <YAxis
                                type="category"
                                dataKey="name"
                                hide={true}
                            />
                            <Tooltip content={<CustomTooltip />} cursor={{ fill: 'transparent' }} />
                            <Legend
                                verticalAlign="bottom"
                                height={36}
                                iconType="circle"
                                iconSize={8}
                                wrapperStyle={{ fontSize: '12px', color: '#6b7280' }}
                            />
                            <Bar
                                dataKey="Anizai Forecast"
                                fill="#0d9488" /* anizai-teal-600 */
                                radius={[0, 4, 4, 0]}
                                barSize={32}
                                label={{ position: 'right', fill: '#0d9488', fontSize: 12, fontWeight: 600, formatter: (val: number) => `${val}%` }}
                            />
                            <Bar
                                dataKey="Market Consensus"
                                fill="#9ca3af" /* gray-400 */
                                radius={[0, 4, 4, 0]}
                                barSize={32}
                                label={{ position: 'right', fill: '#6b7280', fontSize: 12, fontWeight: 500, formatter: (val: number) => `${val}%` }}
                            />
                        </BarChart>
                    </ResponsiveContainer>
                </div>

                {/* Narrative Footer */}
                <div className="mt-4 pt-3 border-t border-gray-50">
                    <p className="text-xs text-gray-500 italic">
                        "Recent legislative approvals carry significantly more weight than broader market sentiment."
                    </p>
                </div>
            </CardContent>
        </Card>
    );
}
