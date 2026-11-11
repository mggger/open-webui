export type EventCategory = 'technology' | 'business' | 'science' | 'culture' | 'sports' | 'other';

export type BigEvent = {
	id: string;
	title: string;
	start: string;
	end?: string;
	description?: string;
	location?: string;
	organiser?: string;
	targetAudience?: string;
	cost?: string;
	participation?: string;
	registrationUrl?: string;
	lastVerified?: string;
	url?: string;
	category: EventCategory;
	sourceId: string;
};

// The string intersection keeps built-in autocomplete while allowing third-party adapter identifiers.
export type EventSourceKind = 'builtin' | 'json' | 'ics' | (string & {});

export type EventSource = {
	id: string;
	name: string;
	kind: EventSourceKind;
	enabled: boolean;
	color: string;
	url?: string;
	homepageUrl?: string;
	readonly?: boolean;
};

export type EventSourceContext = {
	year: number;
	signal?: AbortSignal;
	token?: string;
};

export type EventSourceAdapter = {
	kind: EventSourceKind;
	load: (source: EventSource, context: EventSourceContext) => Promise<BigEvent[]>;
};

export type EventSourceResult = {
	source: EventSource;
	events: BigEvent[];
	error?: string;
};
