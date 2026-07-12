function authorization(_request) {
    const encoded = process.env.KAI_WORKER_AUTH_B64;
    if (!encoded) {
        throw new Error('worker auth secret environment is empty');
    }
    return `Basic ${encoded}`;
}

export default { authorization };
