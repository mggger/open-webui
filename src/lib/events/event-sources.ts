import { WEBUI_API_BASE_URL } from '$lib/constants';
import type {
	BigEvent,
	EventCategory,
	EventSource,
	EventSourceAdapter,
	EventSourceContext,
	EventSourceKind,
	EventSourceResult
} from './types';

export const EVENT_SOURCES_STORAGE_KEY = 'open-webui-big-event-sources-v1';

export const SCHEDULED_EVENT_SOURCE: EventSource = {
	id: 'scheduled-sydney-discovery',
	name: 'Humanitix · Sydney Business & Professional',
	kind: 'scheduled',
	enabled: true,
	color: '#0ea5e9',
	url: `${WEBUI_API_BASE_URL}/big-events?source_type=humanitix`,
	homepageUrl: 'https://humanitix.com/au/events/au--nsw--sydney/businessandprofessional',
	readonly: true
};

const scheduledSource = (
	id: string,
	name: string,
	sourceType: string,
	homepageUrl: string,
	color: string
): EventSource => ({
	id,
	name,
	kind: 'scheduled',
	enabled: true,
	color,
	url: `${WEBUI_API_BASE_URL}/big-events?source_type=${encodeURIComponent(sourceType)}`,
	homepageUrl,
	readonly: true
});

export const DEFAULT_EVENT_SOURCES: EventSource[] = [
	SCHEDULED_EVENT_SOURCE,
	scheduledSource(
		'scheduled-aicd',
		'Australian Institute of Company Directors',
		'aicd',
		'https://www.aicd.com.au/events/all-events.html',
		'#4f46e5'
	),
	scheduledSource(
		'scheduled-aicc-nsw',
		'AICC NSW',
		'aicc-nsw',
		'https://portal.aiccnsw.org.au/all-events/',
		'#0891b2'
	),
	scheduledSource(
		'scheduled-adapt',
		'ADAPT Edge',
		'adapt',
		'https://adapt.com.au/edge-events/',
		'#7c3aed'
	),
	scheduledSource(
		'scheduled-governance-institute',
		'Governance Institute of Australia',
		'governance-institute',
		'https://www.governanceinstitute.com.au/events/',
		'#c2410c'
	),
	scheduledSource(
		'scheduled-acs',
		'Australian Computer Society',
		'acs',
		'https://www.acs.org.au/cpd-education/event-listing.html',
		'#2563eb'
	),
	scheduledSource(
		'scheduled-business-nsw',
		'Business NSW',
		'business-nsw',
		'https://www.businessnsw.com/events/upcoming-events',
		'#059669'
	),
	scheduledSource(
		'scheduled-aisa',
		'Australian Information Security Association',
		'aisa',
		'https://aisa.org.au/',
		'#dc2626'
	),
	scheduledSource(
		'scheduled-isaca-sydney',
		'ISACA Sydney Chapter',
		'isaca-sydney',
		'https://engage.isaca.org/sydneychapter/events',
		'#9333ea'
	)
];

const categories = new Set<EventCategory>([
	'technology',
	'business',
	'science',
	'culture',
	'sports',
	'other'
]);

const normaliseCategory = (value: unknown): EventCategory =>
	typeof value === 'string' && categories.has(value as EventCategory)
		? (value as EventCategory)
		: 'other';

const toDateString = (value: unknown): string => {
	if (typeof value !== 'string' && typeof value !== 'number') return '';
	const raw = String(value);
	if (/^\d{4}-\d{2}-\d{2}/.test(raw)) return raw.slice(0, 10);
	const parsed = new Date(value);
	return Number.isNaN(parsed.getTime()) ? '' : parsed.toISOString().slice(0, 10);
};

const toOptionalString = (value: unknown): string | undefined =>
	typeof value === 'string' && value.trim() ? value.trim() : undefined;

const toHttpUrl = (value: unknown): string | undefined => {
	if (typeof value !== 'string') return undefined;
	try {
		const url = new URL(value);
		return ['http:', 'https:'].includes(url.protocol) ? url.toString() : undefined;
	} catch {
		return undefined;
	}
};

const normaliseRemoteEvent = (
	item: unknown,
	source: EventSource,
	index: number
): BigEvent | null => {
	if (!item || typeof item !== 'object') return null;
	const data = item as Record<string, unknown>;
	const title = String(data.title ?? data.name ?? data.summary ?? '').trim();
	const start = toDateString(data.start ?? data.startDate ?? data.date);
	if (!title || !start) return null;

	return {
		id: String(data.id ?? `${source.id}-${start}-${index}`),
		title,
		start,
		end: toDateString(data.end ?? data.endDate) || undefined,
		description: toOptionalString(data.description),
		location: toOptionalString(data.location),
		organiser: toOptionalString(data.organiser ?? data.organizer),
		targetAudience: toOptionalString(data.targetAudience ?? data.audience),
		cost: toOptionalString(data.cost ?? data.price),
		participation: toOptionalString(data.participation ?? data.howToParticipate),
		registrationUrl: toHttpUrl(data.registrationUrl ?? data.registrationLink ?? data.url),
		lastVerified: toDateString(data.lastVerified ?? data.verifiedAt) || undefined,
		url: toHttpUrl(data.url ?? data.registrationUrl ?? data.registrationLink),
		category: normaliseCategory(data.category),
		sourceId: source.id
	};
};

const fetchText = async (source: EventSource, context: EventSourceContext): Promise<string> => {
	if (!source.url) throw new Error('Source URL is missing');
	const response = await fetch(source.url, { signal: context.signal });
	if (!response.ok) throw new Error(`HTTP ${response.status}`);
	return response.text();
};

const jsonAdapter: EventSourceAdapter = {
	kind: 'json',
	load: async (source, context) => {
		const payload = JSON.parse(await fetchText(source, context));
		const items = Array.isArray(payload) ? payload : payload?.events;
		if (!Array.isArray(items)) throw new Error('Expected a JSON array or an object with events[]');
		return items
			.map((item, index) => normaliseRemoteEvent(item, source, index))
			.filter((item): item is BigEvent => item !== null);
	}
};

const scheduledAdapter: EventSourceAdapter = {
	kind: 'scheduled',
	load: async (source, context) => {
		if (!source.url) throw new Error('Scheduled source URL is missing');
		const response = await fetch(source.url, {
			signal: context.signal,
			headers: context.token ? { Authorization: `Bearer ${context.token}` } : undefined
		});
		if (!response.ok) throw new Error(`HTTP ${response.status}`);
		const payload = await response.json();
		if (!Array.isArray(payload?.events)) throw new Error('Expected an events[] response');
		return payload.events
			.map((item: unknown, index: number) => normaliseRemoteEvent(item, source, index))
			.filter((item: BigEvent | null): item is BigEvent => item !== null)
			.filter((event: BigEvent) => event.start.startsWith(`${context.year}-`));
	}
};

const parseIcsDate = (value: string): string => {
	const match = value.match(/(\d{4})(\d{2})(\d{2})/);
	return match ? `${match[1]}-${match[2]}-${match[3]}` : '';
};

const unescapeIcs = (value: string): string =>
	value.replace(/\\n/gi, ' ').replace(/\\,/g, ',').replace(/\\;/g, ';').replace(/\\\\/g, '\\');

const icsAdapter: EventSourceAdapter = {
	kind: 'ics',
	load: async (source, context) => {
		const text = (await fetchText(source, context)).replace(/\r?\n[ \t]/g, '');
		return text
			.split('BEGIN:VEVENT')
			.slice(1)
			.map((block, index): BigEvent | null => {
				const fields = new Map<string, string>();
				for (const line of block.split(/\r?\n/)) {
					const separator = line.indexOf(':');
					if (separator < 0) continue;
					fields.set(line.slice(0, separator).split(';')[0], line.slice(separator + 1));
				}
				const title = unescapeIcs(fields.get('SUMMARY') ?? '').trim();
				const start = parseIcsDate(fields.get('DTSTART') ?? '');
				if (!title || !start) return null;
				return {
					id: fields.get('UID') ?? `${source.id}-${start}-${index}`,
					title,
					start,
					end: parseIcsDate(fields.get('DTEND') ?? '') || undefined,
					description: unescapeIcs(fields.get('DESCRIPTION') ?? '') || undefined,
					location: unescapeIcs(fields.get('LOCATION') ?? '') || undefined,
					url: toHttpUrl(fields.get('URL')),
					registrationUrl: toHttpUrl(fields.get('URL')),
					category: 'other' as const,
					sourceId: source.id
				};
			})
			.filter((item): item is BigEvent => item !== null);
	}
};

const adapters = new Map<EventSourceKind, EventSourceAdapter>();

/** Registering an adapter is the extension point for webpage-specific event source plugins. */
export const registerEventSourceAdapter = (adapter: EventSourceAdapter): void => {
	adapters.set(adapter.kind, adapter);
};

registerEventSourceAdapter(jsonAdapter);
registerEventSourceAdapter(icsAdapter);
registerEventSourceAdapter(scheduledAdapter);

export const loadEventSources = async (
	sources: EventSource[],
	context: EventSourceContext
): Promise<EventSourceResult[]> =>
	Promise.all(
		sources
			.filter((source) => source.enabled)
			.map(async (source) => {
				try {
					const adapter = adapters.get(source.kind);
					if (!adapter) throw new Error(`No adapter registered for ${source.kind}`);
					return { source, events: await adapter.load(source, context) };
				} catch (error) {
					return {
						source,
						events: [],
						error: error instanceof Error ? error.message : String(error)
					};
				}
			})
	);

export const readStoredEventSources = (storage: Storage): EventSource[] => {
	try {
		const parsed = JSON.parse(storage.getItem(EVENT_SOURCES_STORAGE_KEY) ?? '[]');
		const readonlyIds = new Set(DEFAULT_EVENT_SOURCES.map((source) => source.id));
		const custom = Array.isArray(parsed)
			? parsed.filter(
					(source): source is EventSource =>
						source &&
						typeof source.id === 'string' &&
						typeof source.name === 'string' &&
						typeof source.kind === 'string' &&
						!readonlyIds.has(source.id)
				)
			: [];
		return [
			...DEFAULT_EVENT_SOURCES.map((defaultSource) => {
				const stored = Array.isArray(parsed)
					? parsed.find((source) => source?.id === defaultSource.id)
					: null;
				return {
					...defaultSource,
					enabled: typeof stored?.enabled === 'boolean' ? stored.enabled : defaultSource.enabled
				};
			}),
			...custom
		];
	} catch {
		return DEFAULT_EVENT_SOURCES;
	}
};

export const writeStoredEventSources = (storage: Storage, sources: EventSource[]): void => {
	storage.setItem(
		EVENT_SOURCES_STORAGE_KEY,
		JSON.stringify(
			sources.map((source) =>
				source.readonly ? { id: source.id, kind: source.kind, enabled: source.enabled } : source
			)
		)
	);
};
