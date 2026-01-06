import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '../ui/card';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';
import type { SentimentDataPoint } from '../../types';

interface SentimentAnalysisProps {
    data: SentimentDataPoint[];
}

export function SentimentAnalysis({ data }: SentimentAnalysisProps) {
    return (
        <Card>
            <CardHeader>
                <CardTitle>Sentiment Analysis</CardTitle>
                <CardDescription>Expert vs public sentiment trends over time</CardDescription>
            </CardHeader>
            <CardContent>
                <ResponsiveContainer width="100%" height={250}>
                    <LineChart data={data}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                        <XAxis
                            dataKey="date"
                            tick={{ fontSize: 12 }}
                            stroke="#9ca3af"
                        />
                        <YAxis
                            domain={[0, 100]}
                            tick={{ fontSize: 12 }}
                            stroke="#9ca3af"
                        />
                        <Tooltip
                            contentStyle={{ borderRadius: '8px', border: '1px solid #e5e7eb' }}
                            formatter={(value: number) => `${value}%`}
                        />
                        <Legend />
                        <Line
                            type="monotone"
                            dataKey="expertSentiment"
                            stroke="rgb(59, 130, 246)"
                            strokeWidth={2}
                            dot={{ fill: 'rgb(59, 130, 246)', r: 4 }}
                            name="Expert Sentiment"
                        />
                        <Line
                            type="monotone"
                            dataKey="publicSentiment"
                            stroke="rgb(168, 85, 247)"
                            strokeWidth={2}
                            dot={{ fill: 'rgb(168, 85, 247)', r: 4 }}
                            name="Public Sentiment"
                        />
                    </LineChart>
                </ResponsiveContainer>
                <div className="mt-4 grid grid-cols-2 gap-4">
                    <div className="p-3 bg-blue-50 rounded-lg">
                        <p className="text-xs text-blue-700 font-medium mb-1">Expert Sentiment</p>
                        <p className="text-2xl font-bold text-blue-900">
                            {data[data.length - 1]?.expertSentiment}%
                        </p>
                        <p className="text-xs text-blue-600 mt-1">↑ Trending positive</p>
                    </div>
                    <div className="p-3 bg-purple-50 rounded-lg">
                        <p className="text-xs text-purple-700 font-medium mb-1">Public Sentiment</p>
                        <p className="text-2xl font-bold text-purple-900">
                            {data[data.length - 1]?.publicSentiment}%
                        </p>
                        <p className="text-xs text-purple-600 mt-1">↑ Growing support</p>
                    </div>
                </div>
            </CardContent>
        </Card>
    );
}
