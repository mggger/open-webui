import { WEBUI_API_BASE_URL } from '$lib/constants';

export const getDeepSearchConfig = async (token: string) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/deep-search/config`, {
		method: 'GET',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail ?? err;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const updateDeepSearchConfig = async (token: string, payload: object) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/deep-search/config/update`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify(payload)
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail ?? err;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const generateDeepSearchReport = async (
	token: string,
	payload: object,
	signal?: AbortSignal
) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/deep-search/report`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify(payload),
		signal
	})
		.then(async (res) => {
			if (!res.ok) {
				const detail = await res.json().catch(() => null);
				throw detail ?? res.statusText;
			}
			return res.text();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail ?? err;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};
