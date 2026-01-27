import { useState } from 'react';
import { ArrowLeft, Mail, MessageSquare } from 'lucide-react';
import { Button } from '../components/ui/button';

interface ContactPageProps {
  onBack?: () => void;
}

type ContactForm = {
  firstName: string;
  lastName: string;
  email: string;
  subject: string;
  message: string;
};

export function ContactPage({ onBack }: ContactPageProps) {
  const [formData, setFormData] = useState<ContactForm>({
    firstName: '',
    lastName: '',
    email: '',
    subject: '',
    message: '',
  });

  const [submitted, setSubmitted] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
    const { name, value } = e.target;
    setFormData((prev) => ({
      ...prev,
      [name]: value,
    }));
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    setIsSubmitting(true);

    // In production, send to backend
    console.log('Form submitted:', formData);

    // simulate short delay
    setTimeout(() => {
      setSubmitted(true);
      setIsSubmitting(false);

      // Reset & go back after 2s
      setTimeout(() => {
        setFormData({ firstName: '', lastName: '', email: '', subject: '', message: '' });
        setSubmitted(false);
        onBack?.();
      }, 2000);
    }, 600);
  };

  return (
    <div className="min-h-screen bg-white">
      {/* Header (sticky like About) */}
      <div className="bg-white/80 backdrop-blur border-b border-gray-200 px-6 py-5 sticky top-0 z-20">
        <div className="max-w-6xl mx-auto flex items-center gap-4">
          <button
            onClick={onBack}
            className="flex items-center gap-2 text-gray-600 hover:text-gray-900 transition-colors"
          >
            <ArrowLeft className="w-5 h-5" />
            <span className="font-medium">Back</span>
          </button>
        </div>
      </div>

      {/* Hero */}
      <section className="relative overflow-hidden">
        {/* soft gradient blobs */}
        <div className="absolute inset-0 pointer-events-none">
          <div className="absolute -top-28 -left-28 h-[420px] w-[420px] rounded-full bg-anizai-teal-200/30 blur-3xl" />
          <div className="absolute -bottom-28 -right-28 h-[420px] w-[420px] rounded-full bg-anizai-purple-200/25 blur-3xl" />
          <div className="absolute top-24 right-1/4 h-[260px] w-[260px] rounded-full bg-anizai-blue-200/20 blur-3xl" />
        </div>

        <div className="px-6 pt-14 pb-10">
          <div className="max-w-4xl mx-auto">
            <div className="mb-4 inline-flex items-center gap-2 rounded-full border border-gray-200 bg-white/70 px-3 py-1 text-xs text-gray-600">
              <span className="h-2 w-2 rounded-full bg-anizai-blue-500" />
              Contact
            </div>

            <h1 className="text-4xl sm:text-5xl font-bold tracking-tight text-gray-900">
              Contact <span className="bg-gradient-to-r from-anizai-teal-600 via-anizai-blue-600 to-anizai-purple-600 bg-clip-text text-transparent">Us</span>
            </h1>

            <p className="mt-4 text-lg text-gray-600 max-w-2xl">
              Have a question or feedback? Send us a message and we’ll get back to you as soon as possible.
            </p>
          </div>
        </div>
      </section>

      {/* Content */}
      <div className="px-6 pb-16">
        <div className="max-w-4xl mx-auto">
          {/* Info cards */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-6 mb-10">
            <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm hover:shadow-md transition-shadow">
              <div className="flex items-start gap-3">
                <div className="mt-1">
                  <Mail className="w-5 h-5 text-anizai-blue-600" />
                </div>
                <div>
                  <p className="text-sm font-semibold text-gray-900">Email</p>
                  <p className="text-sm text-gray-600">hello@anizai.com</p>
                </div>
              </div>
            </div>

            <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm hover:shadow-md transition-shadow">
              <div className="flex items-start gap-3">
                <div className="mt-1">
                  <MessageSquare className="w-5 h-5 text-anizai-teal-600" />
                </div>
                <div>
                  <p className="text-sm font-semibold text-gray-900">Response time</p>
                  <p className="text-sm text-gray-600">Usually within 24–48 hours</p>
                </div>
              </div>
            </div>
          </div>

          {/* Form / Success */}
          {!submitted ? (
            <form
              onSubmit={handleSubmit}
              className="rounded-2xl border border-gray-200 bg-white p-7 sm:p-8 shadow-sm"
            >
              <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
                <div>
                  <label htmlFor="firstName" className="block text-sm font-medium text-gray-700 mb-2">
                    First name
                  </label>
                  <input
                    type="text"
                    id="firstName"
                    name="firstName"
                    value={formData.firstName}
                    onChange={handleChange}
                    required
                    className="w-full px-4 py-2.5 border border-gray-300 rounded-lg focus:ring-2 focus:ring-anizai-blue-500 focus:border-transparent outline-none transition"
                    placeholder="First name"
                  />
                </div>

                <div>
                  <label htmlFor="lastName" className="block text-sm font-medium text-gray-700 mb-2">
                    Last name
                  </label>
                  <input
                    type="text"
                    id="lastName"
                    name="lastName"
                    value={formData.lastName}
                    onChange={handleChange}
                    required
                    className="w-full px-4 py-2.5 border border-gray-300 rounded-lg focus:ring-2 focus:ring-anizai-blue-500 focus:border-transparent outline-none transition"
                    placeholder="Last name"
                  />
                </div>

                <div className="md:col-span-2">
                  <label htmlFor="email" className="block text-sm font-medium text-gray-700 mb-2">
                    Email
                  </label>
                  <input
                    type="email"
                    id="email"
                    name="email"
                    value={formData.email}
                    onChange={handleChange}
                    required
                    className="w-full px-4 py-2.5 border border-gray-300 rounded-lg focus:ring-2 focus:ring-anizai-blue-500 focus:border-transparent outline-none transition"
                    placeholder="you@email.com"
                  />
                </div>

                <div className="md:col-span-2">
                  <label htmlFor="subject" className="block text-sm font-medium text-gray-700 mb-2">
                    Subject
                  </label>
                  <input
                    type="text"
                    id="subject"
                    name="subject"
                    value={formData.subject}
                    onChange={handleChange}
                    required
                    className="w-full px-4 py-2.5 border border-gray-300 rounded-lg focus:ring-2 focus:ring-anizai-blue-500 focus:border-transparent outline-none transition"
                    placeholder="What is this about?"
                  />
                </div>

                <div className="md:col-span-2">
                  <label htmlFor="message" className="block text-sm font-medium text-gray-700 mb-2">
                    Message
                  </label>
                  <textarea
                    id="message"
                    name="message"
                    value={formData.message}
                    onChange={handleChange}
                    required
                    rows={6}
                    className="w-full px-4 py-2.5 border border-gray-300 rounded-lg focus:ring-2 focus:ring-anizai-blue-500 focus:border-transparent outline-none transition resize-none"
                    placeholder="Tell us more..."
                  />
                </div>
              </div>

              <div className="mt-6">
                <Button
                  type="submit"
                  variant="primary"
                  size="lg"
                  className="w-full"
                  disabled={isSubmitting}
                >
                  {isSubmitting ? 'Sending…' : 'Send message'}
                </Button>

                <p className="mt-3 text-xs text-gray-400 text-center">
                  By sending this message, you agree to our Terms and Privacy Policy.
                </p>
              </div>
            </form>
          ) : (
            <div className="rounded-2xl border border-green-200 bg-green-50 p-8 text-center">
              <div className="mb-4">
                <div className="w-12 h-12 rounded-full bg-green-100 flex items-center justify-center mx-auto">
                  <svg className="w-6 h-6 text-green-700" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                  </svg>
                </div>
              </div>
              <h3 className="text-lg font-semibold text-green-900 mb-2">Message sent</h3>
              <p className="text-green-800 mb-3">Thanks for reaching out. We’ll get back to you soon.</p>
              <p className="text-sm text-green-700">Redirecting…</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
