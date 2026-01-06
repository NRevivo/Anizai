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

    return (
        <Card>
            <CardHeader>
                <CardTitle>Market vs Anizai</CardTitle>
                <CardDescription>Comparison with prediction market consensus</CardDescription>
            </CardHeader>
            <CardContent>
                <ResponsiveContainer width="100%" height={200}>
                    <BarChart data={data} layout="vertical">
                        <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                        <XAxis type="number" domain={[0, 100]} />
                        <YAxis type="category" dataKey="name" />
                        <Tooltip
                            formatter={(value: number) => `${value.toFixed(1)}%`}
                            contentStyle={{ borderRadius: '8px', border: '1px solid #e5e7eb' }}
                        />
                        <Legend />
                        <Bar dataKey="Market" fill="#94a3b8" radius={[0, 4, 4, 0]} />
                        <Bar dataKey="Anizai" fill="url(#barGradient)" radius={[0, 4, 4, 0]} />
                        <defs>
                            <linearGradient id="barGradient" x1="0" y1="0" x2="1" y2="0">
                                <stop offset="0%" stopColor="rgb(20, 184, 166)" />
                                <stop offset="50%" stopColor="rgb(59, 130, 246)" />
                                <stop offset="100%" stopColor="rgb(168, 85, 247)" />
                            </linearGradient>
                        </defs>
                    </BarChart>
                </ResponsiveContainer>
                <div className="mt-4 p-3 bg-gray-50 rounded-lg">
                    <p className="text-sm text-gray-700">
                        <span className="font-medium">Analysis:</span> {differenceText}
                    </p>
                </div>
            </CardContent>
        </Card>
    );
}
