% filepath: sandbox/moving_average_filter.m

function moving_average = calculate_moving_average(signal, window_size)
    % Check if input signal is a 1D numeric array and has at least one element
    if ~isnumeric(signal) || size(signal) ~= [length(signal), 1]
        error('Input signal must be a 1D numeric array with at least one element.');
    end
    
    % Check if window size is an integer greater than or equal to 1
    if ~isscalar(window_size) || window_size < 1
        error('Window size must be an integer greater than or equal to 1.');
    end
    
    % Initialize moving_average array with zeros of the same length as signal
    moving_average = zeros(size(signal));
    
    % Calculate moving average for each window
    for i = 1:length(signal) - window_size + 1
        window = signal(i:i+window_size-1);
        moving_average(i) = sum(window) / window_size;
    end
    
    return moving_average;
end

% Test script for moving_average_filter

% Import the module under test
addpath('.');
result = moving_average_filter([1, 2, 3], 2);

% Expected output for a window size of 2
expected_output = [0.5, 1.33333333, 2];

% Assert that the result matches the expected output
assert(abs(result - expected_output) < 1e-9, 'Test 1 failed');

disp('All tests passed.');