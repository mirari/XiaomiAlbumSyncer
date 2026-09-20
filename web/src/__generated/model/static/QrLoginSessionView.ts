export interface QrLoginSessionView {
    readonly sessionId: string;
    readonly qrUrl: string;
    readonly expiresIn: number;
}
