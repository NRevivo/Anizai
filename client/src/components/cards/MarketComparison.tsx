import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '../ui/card';
import { StateMessage } from '../ui/StateMessage';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';

interface MarketComparisonProps {
    anizaiProbability: number;
    marketProbability?: number;
}

export function MarketComparison({ anizaiProbability, marketProbability }: MarketComparisonProps) {
    const hasMarketProbability = marketProbability != null;
    const anizaiPct = anizaiProbability * 100;
    const marketPct = hasMarketProbability ? marketProbability * 100 : 0;
    const data = [
        {
            name: 'Compare',
            'Market Consensus': marketPct,
            'Anizai Forecast': anizaiPct,
        },
    ];

    const difference = hasMarketProbability ? anizaiPct - marketPct : 0;
    const isBullish = difference > 0;

    const insightTitle = !hasMarketProbability
        ? 'No market benchmark available'
        : isBullish
            ? `Anizai is ${difference.toFixed(1)} points above the market benchmark`
            : `Anizai is ${Math.abs(difference).toFixed(1)} points below the market benchmark`;

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
        <Card className="h-full max-w-full overflow-hidden border-gray-200 bg-white shadow-sm flex flex-col">
            <CardHeader className="p-4 sm:p-5 pb-2">
                <CardTitle className="text-base font-semibold text-gray-900 leading-tight break-words">
                    {insightTitle}
                </CardTitle>
                <CardDescription className="text-xs text-gray-500">
                    Secondary comparison against prediction-market data
                </CardDescription>
            </CardHeader>
            <CardContent className="p-4 sm:p-5 pt-2 flex-1 flex flex-col justify-center">
                {hasMarketProbability ? (
                    <div className="w-full h-[150px] overflow-hidden">
                        <ResponsiveContainer width="100%" height="100%">
                            <BarChart
                                data={data}
                                layout="vertical"
                                margin={{ top: 0, right: 20, left: 0, bottom: 0 }}
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
                                    height={34}
                                    iconType="circle"
                                    iconSize={8}
                                    wrapperStyle={{ fontSize: '12px', color: '#6b7280' }}
                                />
                                <Bar
                                    dataKey="Anizai Forecast"
                                    fill="#0d9488"
                                    radius={[0, 4, 4, 0]}
                                    barSize={24}
                                    label={{ position: 'right', fill: '#0d9488', fontSize: 11, fontWeight: 600, formatter: (val: number) => `${val.toFixed(1)}%` }}
                                />
                                <Bar
                                    dataKey="Market Consensus"
                                    fill="#9ca3af"
                                    radius={[0, 4, 4, 0]}
                                    barSize={24}
                                    label={{ position: 'right', fill: '#6b7280', fontSize: 11, fontWeight: 500, formatter: (val: number) => `${val.toFixed(1)}%` }}
                                />
                            </BarChart>
                        </ResponsiveContainer>
                    </div>
                ) : (
                    <StateMessage
                        compact
                        title="No market benchmark"
                        description="Comparable market probability data is not available for this forecast."
                    />
                )}

                {hasMarketProbability ? (
                    <div className="mt-4 pt-3 border-t border-gray-50">
                        <p className="text-xs text-gray-500">
                            Use this as context. The forecast result above remains the primary answer.
                        </p>
                    </div>
                ) : null}
            </CardContent>
        </Card>
    );
}
