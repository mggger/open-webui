import { afterEach, describe, expect, it, vi } from 'vitest';

import {
	SCHEDULED_EVENT_SOURCE,
	EVENT_SOURCES_STORAGE_KEY,
	loadEventSources,
	readStoredEventSources,
	writeStoredEventSources
} from './event-sources';
import type { EventSource } from './types';

const memoryStorage = (): Storage => {
	const values = new Map<string, string>();
	return {
		get length() {
			return values.size;
		},
		clear: () => values.clear(),
		getItem: (key) => values.get(key) ?? null,
		key: (index) => [...values.keys()][index] ?? null,
		removeItem: (key) => values.delete(key),
		setItem: (key, value) => values.set(key, value)
	};
};

afterEach(() => {
	vi.unstubAllGlobals();
});

describe('event source adapters', () => {
	it('normalises a JSON feed', async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn(
				async () =>
					new Response(
						JSON.stringify({
							events: [
								{
									name: 'Launch day',
									date: '2030-04-12',
									category: 'technology',
									organizer: 'Demo organiser',
									audience: 'CIOs and CTOs',
									price: '$100',
									registrationLink: 'https://example.com/register'
								}
							]
						})
					)
			)
		);
		const source: EventSource = {
			id: 'json-source',
			name: 'JSON source',
			kind: 'json',
			enabled: true,
			color: '#000',
			url: 'https://example.com/events.json'
		};

		const [result] = await loadEventSources([source], { year: 2030 });

		expect(result.events).toMatchObject([
			{
				title: 'Launch day',
				start: '2030-04-12',
				category: 'technology',
				organiser: 'Demo organiser',
				targetAudience: 'CIOs and CTOs',
				cost: '$100',
				registrationUrl: 'https://example.com/register',
				sourceId: 'json-source'
			}
		]);
	});

	it('does not expose unsafe links from remote feeds', async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn(
				async () =>
					new Response(
						JSON.stringify([{ title: 'Unsafe', date: '2030-01-02', url: 'javascript:alert(1)' }])
					)
			)
		);
		const source: EventSource = {
			id: 'unsafe-source',
			name: 'Unsafe source',
			kind: 'json',
			enabled: true,
			color: '#000',
			url: 'https://example.com/events.json'
		};

		const [result] = await loadEventSources([source], { year: 2030 });

		expect(result.events[0].url).toBeUndefined();
		expect(result.events[0].registrationUrl).toBeUndefined();
	});

	it('parses an ICS calendar', async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn(
				async () =>
					new Response(
						'BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:demo-1\r\nDTSTART;VALUE=DATE:20300718\r\nSUMMARY:Open Summit\r\nLOCATION:Shanghai\r\nEND:VEVENT\r\nEND:VCALENDAR'
					)
			)
		);
		const source: EventSource = {
			id: 'ics-source',
			name: 'ICS source',
			kind: 'ics',
			enabled: true,
			color: '#000',
			url: 'https://example.com/events.ics'
		};

		const [result] = await loadEventSources([source], { year: 2030 });

		expect(result.events[0]).toMatchObject({
			id: 'demo-1',
			title: 'Open Summit',
			start: '2030-07-18',
			location: 'Shanghai'
		});
	});

	it('loads the authenticated event database', async () => {
		const fetchMock = vi.fn(async (...args: [string, RequestInit?]) => {
			void args;
			return new Response(
				JSON.stringify({
					events: [
						{
							title: 'Sydney CIO Forum',
							date: '2030-08-10',
							registrationUrl: 'https://example.com/register'
						}
					]
				})
			);
		});
		vi.stubGlobal('fetch', fetchMock);

		const [result] = await loadEventSources([SCHEDULED_EVENT_SOURCE], {
			year: 2030,
			token: 'test-token'
		});

		expect(result.events).toHaveLength(1);
		expect(fetchMock.mock.calls[0][1]?.headers).toMatchObject({
			Authorization: 'Bearer test-token'
		});
	});
});

describe('event source persistence', () => {
	it('persists custom sources and the default source enabled state', () => {
		const storage = memoryStorage();
		const sources: EventSource[] = [
			{ ...SCHEDULED_EVENT_SOURCE, enabled: false },
			{
				id: 'custom',
				name: 'Custom',
				kind: 'json',
				enabled: true,
				color: '#fff',
				url: 'https://example.com/events.json'
			}
		];

		writeStoredEventSources(storage, sources);

		expect(storage.getItem(EVENT_SOURCES_STORAGE_KEY)).toContain('custom');
		expect(readStoredEventSources(storage)).toMatchObject([
			{ id: SCHEDULED_EVENT_SOURCE.id, enabled: false },
			{ id: 'custom', enabled: true }
		]);
	});
});
