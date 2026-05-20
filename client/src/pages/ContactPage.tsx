import { useState } from 'react';
import { Button } from '../components/ui/button';
import { PageShell, PageHeader, type PageShellProps } from '../components/site/PageShell';

interface ContactPageProps extends Omit<PageShellProps, 'children'> {}

type ContactForm = {
    firstName: string;
    lastName: string;
    email: string;
    subject: string;
    message: string;
};

const INITIAL_FORM: ContactForm = {
    firstName: '',
    lastName: '',
    email: '',
    subject: '',
    message: '',
};

export function ContactPage(props: ContactPageProps) {
    const [formData, setFormData] = useState<ContactForm>(INITIAL_FORM);
    const [submitted, setSubmitted] = useState(false);
    const [isSubmitting, setIsSubmitting] = useState(false);

    const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
        const { name, value } = e.target;
        setFormData((prev) => ({ ...prev, [name]: value }));
    };

    const handleSubmit = (e: React.FormEvent) => {
        e.preventDefault();
        setIsSubmitting(true);
        // In production, send to backend
        console.log('Form submitted:', formData);
        setTimeout(() => {
            setSubmitted(true);
            setIsSubmitting(false);
            setTimeout(() => {
                setFormData(INITIAL_FORM);
                setSubmitted(false);
            }, 4000);
        }, 600);
    };

    return (
        <PageShell {...props}>
            <PageHeader
                eyebrow="Contact"
                title={<>Get in touch.</>}
                description="Questions about access, feedback on a forecast, or interest in the methodology — send a note and we’ll get back to you."
            />

            <section className="w-full px-6 pb-20">
                <div className="max-w-5xl mx-auto grid grid-cols-1 lg:grid-cols-3 gap-10">
                    <aside className="lg:col-span-1 space-y-6">
                        <InfoLine label="Email" value="hello@anizai.com" />
                        <InfoLine label="Response time" value="Usually within 24–48 hours" />
                        <InfoLine label="Status" value="Currently in private beta" />
                    </aside>

                    <div className="lg:col-span-2">
                        {submitted ? (
                            <div className="rounded-2xl bg-white p-8 ring-1 ring-slate-900/[0.05] shadow-[0_4px_24px_rgba(15,23,42,0.05)]">
                                <div className="inline-flex h-10 w-10 items-center justify-center rounded-full bg-anizai-teal-50">
                                    <svg
                                        className="w-5 h-5 text-anizai-teal-700"
                                        viewBox="0 0 24 24"
                                        fill="none"
                                        stroke="currentColor"
                                        strokeWidth={2}
                                    >
                                        <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                                    </svg>
                                </div>
                                <h3 className="mt-4 text-xl font-medium tracking-tight text-gray-900">
                                    Message sent.
                                </h3>
                                <p className="mt-2 max-w-md text-[14.5px] leading-[1.65] text-slate-600">
                                    Thanks for reaching out — we’ll get back to you within
                                    a couple of days.
                                </p>
                            </div>
                        ) : (
                            <form
                                onSubmit={handleSubmit}
                                className="rounded-2xl bg-white p-7 lg:p-8 ring-1 ring-slate-900/[0.05] shadow-[0_4px_24px_rgba(15,23,42,0.05)] space-y-5"
                            >
                                <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
                                    <Field
                                        id="firstName"
                                        label="First name"
                                        value={formData.firstName}
                                        onChange={handleChange}
                                    />
                                    <Field
                                        id="lastName"
                                        label="Last name"
                                        value={formData.lastName}
                                        onChange={handleChange}
                                    />
                                </div>
                                <Field
                                    id="email"
                                    label="Email"
                                    type="email"
                                    value={formData.email}
                                    onChange={handleChange}
                                    placeholder="you@company.com"
                                />
                                <Field
                                    id="subject"
                                    label="Subject"
                                    value={formData.subject}
                                    onChange={handleChange}
                                    placeholder="What is this about?"
                                />
                                <div>
                                    <Label htmlFor="message">Message</Label>
                                    <textarea
                                        id="message"
                                        name="message"
                                        value={formData.message}
                                        onChange={handleChange}
                                        required
                                        rows={6}
                                        placeholder="Tell us more…"
                                        className="mt-2 w-full rounded-lg border border-slate-200 bg-white px-4 py-2.5 text-[14.5px] text-gray-900 placeholder:text-slate-400 focus:border-anizai-purple-400 focus:ring-2 focus:ring-anizai-purple-100 outline-none transition resize-none"
                                    />
                                </div>

                                <div className="pt-2 flex flex-col sm:flex-row items-start sm:items-center gap-4">
                                    <Button
                                        type="submit"
                                        disabled={isSubmitting}
                                        className="h-11 px-7 text-[14.5px] font-medium bg-gray-900 text-white hover:bg-gray-800 rounded-lg shadow-[0_1px_2px_rgba(15,23,42,0.08)] disabled:opacity-60"
                                    >
                                        {isSubmitting ? 'Sending…' : 'Send message'}
                                    </Button>
                                    <p className="text-[12px] text-slate-500">
                                        By sending, you agree to our Terms and Privacy Policy.
                                    </p>
                                </div>
                            </form>
                        )}
                    </div>
                </div>
            </section>
        </PageShell>
    );
}

function InfoLine({ label, value }: { label: string; value: string }) {
    return (
        <div>
            <p className="text-[10.5px] font-medium uppercase tracking-[0.14em] text-slate-400">
                {label}
            </p>
            <p className="mt-1.5 text-[14.5px] text-gray-900">{value}</p>
        </div>
    );
}

function Label({ htmlFor, children }: { htmlFor: string; children: React.ReactNode }) {
    return (
        <label htmlFor={htmlFor} className="block text-[12px] font-medium text-slate-600">
            {children}
        </label>
    );
}

interface FieldProps {
    id: keyof ContactForm;
    label: string;
    value: string;
    onChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
    type?: string;
    placeholder?: string;
}

function Field({ id, label, value, onChange, type = 'text', placeholder }: FieldProps) {
    return (
        <div>
            <Label htmlFor={id}>{label}</Label>
            <input
                id={id}
                name={id}
                type={type}
                value={value}
                onChange={onChange}
                required
                placeholder={placeholder ?? label}
                className="mt-2 w-full rounded-lg border border-slate-200 bg-white px-4 py-2.5 text-[14.5px] text-gray-900 placeholder:text-slate-400 focus:border-anizai-purple-400 focus:ring-2 focus:ring-anizai-purple-100 outline-none transition"
            />
        </div>
    );
}
