import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '../ui/card';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';

interface MarketComparisonProps {
    anizaiProbability: number;
    marketProbability: number;
}

export function MarketComparison({ anizaiProbability, marketProbability }: MarketComparisonProps) {
    const data = [
        {
            name: 'Probability',
            'Market': marketProbability,
            'Anizai': anizaiProbability,
        },
    ];

    const difference = anizaiProbability - marketProbability;
    const differenceText = difference > 0
        ? `Anizai is ${difference.toFixed(1)}% more bullish than the market`
        : `Anizai is ${Math.abs(difference).toFixed(1)}% more bearish than the market`;

    const CustomTooltip = ({ active, payload }: any) => {
        if (active && payload && payload.length) {
            return (
                <div className="bg-white border border-gray-200 rounded-lg p-3 shadow-sm">
                    <p className="text-sm font-medium text-gray-900">{payload[0].name}</p>
                    <p className="text-sm text-gray-600">{`${payload[0].value.toFixed(1)}%`}</p>
                </div>
            );
        }
        return null;
    };

    return (
        <Card>
            <CardHeader>
                <CardTitle>Market vs Anizai</CardTitle>
                <CardDescription>Comparison with prediction market consensus</CardDescription>
            </CardHeader>
            <CardContent className="pt-4">
                <div className="w-full" style={{ height: 220 }}>
                    <ResponsiveContainer width="100%" height="100%">
                        <BarChart
                            data={data}
                            layout="vertical"
                            margin={{ top: 5, right: 20, left: 60, bottom: 5 }}
                        >
                            <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" horizontal={false} />
                            <XAxis
                                type="number"
                                domain={[0, 100]}
                                tick={{ fontSize: 12, fill: '#6b7280' }}
                                axisLine={{ stroke: '#e5e7eb' }}
                            />
                            <YAxis
                                type="category"
                                dataKey="name"
                                tick={{ fontSize: 12, fill: '#6b7280' }}
                                axisLine={{ stroke: '#e5e7eb' }}
                                width={50}
                            />
                            <Tooltip content={<CustomTooltip />} />
                            <Legend
                                wrapperStyle={{ paddingTop: '10px' }}
                                iconType="circle"
                            />
                            <Bar
                                dataKey="Market"
                                fill="#94a3b8"
                                radius={[0, 4, 4, 0]}
                                barSize={24}
                            />
                            <Bar
                                dataKey="Anizai"
                                fill="#14b8a6"
                                radius={[0, 4, 4, 0]}
                                barSize={24}
                            />
                        </BarChart>
                    </ResponsiveContainer>
                </div>
                <div className="mt-4 p-3 bg-gray-50 rounded-lg border border-gray-100">
                    <p className="text-sm text-gray-700">
                        <span className="font-medium">Analysis:</span> {differenceText}
                    </p>
                </div>
            </CardContent>
        </Card>
    );
}
